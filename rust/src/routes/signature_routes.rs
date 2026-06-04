// routes/signature_routes.rs  <- routes/signature_routes.py
//! Signature routes — CRUD for the user's saved visual signatures (proof-batch P13).
//!
//! Signatures are reusable image stamps (drawn once, applied to many things):
//! PDF form fields, email composition, document insertion. Each signature is
//! stored as a base64 PNG so it can be embedded inline anywhere without a
//! separate fetch.
//!
//! Faithful translation of `routes/signature_routes.py`'s `setup_signature_routes`
//! factory. Three handlers over the `signatures` table (raw `rusqlite`, mirroring
//! the SQLAlchemy `Signature` ORM).
//!
//! ## Shape (the integration substrate)
//! * `setup_signature_routes() -> Router<AppState>` mirrors the Python factory,
//!   which takes no args. The Python `APIRouter(tags=["signatures"])` has **no**
//!   `prefix`, so every handler already carries its absolute `/api/signatures...`
//!   path; the `.route(...)` calls use those paths verbatim.
//! * Auth is `get_current_user` only — the load-bearing `None` case (auth disabled
//!   / anonymous) is preserved by reading `Option<Extension<CurrentUser>>`. There is
//!   no `require_user` / `Depends`. Ownership is enforced inline exactly as the
//!   Python does: `list` filters `WHERE owner = ?` only when a user resolves;
//!   `delete` raises 403 (`"Not your signature"`) when `user && sig.owner != user`.
//! * `raise HTTPException(s, d)` -> `return Err(HttpException::new(s, d))`; the
//!   `web`-gated `IntoResponse for HttpException` renders FastAPI's `{"detail": d}`.
//! * `req: SignatureCreate` (pydantic) -> a local `#[derive(Deserialize)]` struct
//!   read from the JSON body.
//!
//! ## Encryption at rest (EncryptedText)
//! `data_png` and `svg` are `EncryptedText` columns: SQLAlchemy transparently
//! Fernet-encrypts on write and decrypts on read. The raw-`rusqlite` translation
//! reproduces that explicitly — [`crate::src::secret_storage::encrypt`] on INSERT,
//! [`crate::src::secret_storage::decrypt`] on SELECT — so the on-disk bytes and the
//! plaintext seen by callers match the ORM exactly. (The create response is built
//! from the plaintext `b64` we already hold, mirroring `db.refresh(sig)` returning
//! the decrypted column.)
//!
//! ## No path collision
//! Every route is under `/api/signatures*`, a prefix the inline `web/mod.rs` subset
//! never touches, so the aggregator merges this router without an axum
//! duplicate-`method`+`path` panic.


use axum::extract::{Path, State};
use axum::response::{IntoResponse, Response};
use axum::routing::{delete, get};
use axum::{Extension, Json, Router};
use base64::Engine as _;
use once_cell::sync::Lazy;
use regex::Regex;
use rusqlite::OptionalExtension;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::routes::{AppState, CurrentUser, HttpException};

/// `_DATA_URL_RE = re.compile(r'^data:image/(?P<fmt>png|jpeg|jpg);base64,(?P<data>.+)$',
///                            re.IGNORECASE | re.DOTALL)`
///
/// `(?is)` = case-insensitive (`re.IGNORECASE`) + dot-matches-newline (`re.DOTALL`),
/// so the greedy `(?P<data>.+)$` captures a multi-line base64 payload after the
/// `data:image/<fmt>;base64,` prefix. `re.match` anchors at the start (the leading
/// `^`); the trailing `$` anchors at the end, matching Python.
static DATA_URL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?is)^data:image/(?P<fmt>png|jpeg|jpg);base64,(?P<data>.+)$").unwrap()
});

/// `class SignatureCreate(BaseModel)` — the JSON body for `POST /api/signatures`.
///
/// `name` defaults to absent (`Optional[str] = None`); `data` is required (the
/// base64 PNG, with or without the `data:image/png;base64,` prefix); `width`,
/// `height`, `svg` are all optional. serde's `#[serde(default)]` reproduces
/// "absent -> None" for the optionals; a missing `data` is a 422 in FastAPI (the
/// request-validation error), which the JSON extractor surfaces.
#[derive(Debug, Deserialize)]
struct SignatureCreate {
    #[serde(default)]
    name: Option<String>,
    /// base64 PNG, with or without `data:image/png;base64,` prefix.
    data: String,
    #[serde(default)]
    width: Option<i64>,
    #[serde(default)]
    height: Option<i64>,
    #[serde(default)]
    svg: Option<String>,
}

/// `setup_signature_routes()` — assemble the signatures router.
///
/// The Python registers, in this order: `GET /api/signatures`,
/// `POST /api/signatures`, `DELETE /api/signatures/{sig_id}`. The `APIRouter` has no
/// `prefix`, so the absolute paths are written directly on each `.route(...)`.
pub fn setup_signature_routes() -> Router<AppState> {
    Router::new()
        .route("/api/signatures", get(list_signatures).post(create_signature))
        .route("/api/signatures/:sig_id", delete(delete_signature))
}

/// `GET /api/signatures` — list the caller's saved signatures, newest first.
///
/// `user = get_current_user(request)`; when `user is not None` the query is
/// **strictly** scoped to `Signature.owner == user` (the comment in the Python
/// notes the previous OR predicate leaked every null-owner signature to every
/// user). When `user is None` (auth disabled), every row is returned. Ordered by
/// `created_at DESC`.
async fn list_signatures(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    let user: Option<String> = user.map(|Extension(CurrentUser(u))| u);
    let conn = session_local()?;

    // `q = db.query(Signature); if user is not None: q = q.filter(Signature.owner == user)`
    // `sigs = q.order_by(Signature.created_at.desc()).all()`.
    let sql = if user.is_some() {
        "SELECT id, name, data_png, width, height, created_at \
         FROM signatures WHERE owner = ?1 ORDER BY created_at DESC"
    } else {
        "SELECT id, name, data_png, width, height, created_at \
         FROM signatures ORDER BY created_at DESC"
    };
    let mut stmt = conn.prepare(sql).map_err(db_500)?;

    let map_row = |r: &rusqlite::Row<'_>| -> rusqlite::Result<Value> {
        let id: String = r.get(0)?;
        let name: Option<String> = r.get(1)?;
        // `data_png` is `EncryptedText` — decrypt the stored value to plaintext b64.
        let data_png_enc: String = r.get(2)?;
        let width: Option<i64> = r.get(3)?;
        let height: Option<i64> = r.get(4)?;
        let created_at: Option<String> = r.get(5)?;
        Ok(to_dict(
            &id,
            name.as_deref(),
            &crate::src::secret_storage::decrypt(&data_png_enc),
            width,
            height,
            created_at.as_deref(),
        ))
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

    Ok(Json(json!({ "signatures": rows })).into_response())
}

/// `POST /api/signatures` — save a new signature.
///
/// Strips an optional `data:image/<fmt>;base64,` prefix, then strictly base64-
/// decodes the payload; a non-base64 / empty payload is a **400**. Stores the
/// base64 string (no `data:` prefix) Fernet-encrypted, with the caller as owner.
async fn create_signature(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Json(req): Json<SignatureCreate>,
) -> Result<Response, HttpException> {
    let user: Option<String> = user.map(|Extension(CurrentUser(u))| u);

    // `raw = (req.data or "").strip()`.
    let raw = req.data.trim();
    // `m = _DATA_URL_RE.match(raw); b64 = m.group("data") if m else raw`.
    let b64: String = match DATA_URL_RE.captures(raw) {
        Some(caps) => caps.name("data").map(|m| m.as_str()).unwrap_or(raw).to_string(),
        None => raw.to_string(),
    };

    // `payload = base64.b64decode(b64, validate=True); if not payload: raise ValueError`
    // `except Exception: raise HTTPException(400, "...")`.
    //
    // `validate=True` rejects any character outside the standard base64 alphabet;
    // base64 0.22's `STANDARD` engine does the same (and requires canonical padding).
    // An empty decoded payload is the explicit `if not payload` 400 branch.
    match base64::engine::general_purpose::STANDARD.decode(b64.as_bytes()) {
        Ok(payload) if !payload.is_empty() => {}
        _ => {
            return Err(HttpException::new(
                400,
                "Signature data must be base64-encoded PNG bytes",
            ))
        }
    }

    // `name=(req.name or "Signature").strip() or "Signature"`.
    let name = {
        let candidate = req.name.unwrap_or_else(|| "Signature".to_string());
        let trimmed = candidate.trim();
        if trimmed.is_empty() {
            "Signature".to_string()
        } else {
            trimmed.to_string()
        }
    };

    let sig_id = uuid::Uuid::new_v4().to_string();
    // TimestampMixin: `created_at`/`updated_at` default to `datetime.utcnow` at flush.
    let now = crate::pydatetime::utcnow_naive_iso();

    // `data_png`/`svg` are `EncryptedText` — encrypt on write. `svg=None` stays NULL
    // (process_bind_param returns None for None); a present svg is encrypted.
    let data_png_enc = crate::src::secret_storage::encrypt(&b64);
    let svg_enc: Option<String> = req
        .svg
        .as_deref()
        .map(crate::src::secret_storage::encrypt);

    let conn = session_local()?;
    // `db.add(sig); db.commit()`. A failure -> rollback + 500 (the Python re-raises
    // `HTTPException(500, f"Failed to save signature: {e}")`).
    if let Err(e) = conn.execute(
        "INSERT INTO signatures \
           (id, owner, name, data_png, width, height, svg, created_at, updated_at) \
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?8)",
        rusqlite::params![
            sig_id,
            user,
            name,
            data_png_enc,
            req.width,
            req.height,
            svg_enc,
            now,
        ],
    ) {
        crate::pylog::error(&format!("Failed to save signature: {e}"));
        return Err(HttpException::new(500, format!("Failed to save signature: {e}")));
    }

    // `db.refresh(sig); return _to_dict(sig)` — the refreshed row decrypts `data_png`
    // back to `b64`, exactly the plaintext we already hold, so build the dict from it.
    Ok(Json(to_dict(
        &sig_id,
        Some(&name),
        &b64,
        req.width,
        req.height,
        Some(&now),
    ))
    .into_response())
}

/// `DELETE /api/signatures/{sig_id}` — delete a signature.
///
/// 404 when the row is missing; **403** (`"Not your signature"`) when a resolved
/// user does not own it (note: this is 403, distinct from the `compare`/`list`
/// strict-404 owner scope — it mirrors the Python exactly).
async fn delete_signature(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(sig_id): Path<String>,
) -> Result<Response, HttpException> {
    let user: Option<String> = user.map(|Extension(CurrentUser(u))| u);
    let conn = session_local()?;

    // `sig = db.query(Signature).filter(Signature.id == sig_id).first()`.
    let owner: Option<Option<String>> = conn
        .query_row(
            "SELECT owner FROM signatures WHERE id = ?1",
            rusqlite::params![sig_id],
            |r| r.get::<_, Option<String>>(0),
        )
        .optional()
        .map_err(db_500)?;

    // `if not sig: raise HTTPException(404, "Signature not found")`.
    let owner = match owner {
        Some(o) => o,
        None => return Err(HttpException::new(404, "Signature not found")),
    };
    // `if user and sig.owner != user: raise HTTPException(403, "Not your signature")`.
    if let Some(u) = user.as_deref() {
        if owner.as_deref() != Some(u) {
            return Err(HttpException::new(403, "Not your signature"));
        }
    }

    // `db.delete(sig); db.commit()`. A failure -> rollback + 500.
    if let Err(e) = conn.execute(
        "DELETE FROM signatures WHERE id = ?1",
        rusqlite::params![sig_id],
    ) {
        return Err(HttpException::new(500, format!("Failed to delete signature: {e}")));
    }
    Ok(Json(json!({ "deleted": sig_id })).into_response())
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// `_to_dict(s: Signature)` — render a signature row as the API JSON dict.
///
/// `data_png` is the **plaintext** base64 (already decrypted by the caller);
/// `data_url` re-attaches the `data:image/png;base64,` prefix. `created_at` is
/// `s.created_at.isoformat() + "Z"` (note the `Z` suffix — distinct from the plain
/// `isoformat` used elsewhere), or `null` when absent. Key order matches the Python
/// dict literal exactly (`serde_json::json!` preserves insertion order here because
/// the crate is built with the `preserve_order` feature).
fn to_dict(
    id: &str,
    name: Option<&str>,
    data_png_plain: &str,
    width: Option<i64>,
    height: Option<i64>,
    created_at: Option<&str>,
) -> Value {
    json!({
        "id": id,
        "name": name,
        "data_url": format!("data:image/png;base64,{data_png_plain}"),
        "width": width,
        "height": height,
        // `(s.created_at.isoformat() + "Z") if s.created_at else None`.
        "created_at": iso_z_or_null(created_at),
    })
}

/// `(dt.isoformat() + "Z") if dt else None` — render a stored SQLite datetime with
/// Python's `isoformat`, append `Z`, or `null` when absent/empty.
fn iso_z_or_null(stored: Option<&str>) -> Value {
    match stored.filter(|s| !s.is_empty()) {
        Some(s) => json!(format!("{}Z", crate::pydatetime::to_isoformat(s))),
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
    crate::pylog::error(&format!("signature_routes DB error: {e}"));
    HttpException::new(500, "Internal Server Error")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn data_url_re_strips_prefix_and_captures_payload() {
        // `m.group("data")` for a data: URL.
        let caps = DATA_URL_RE.captures("data:image/png;base64,AAAA").unwrap();
        assert_eq!(caps.name("data").unwrap().as_str(), "AAAA");
        // Case-insensitive (re.IGNORECASE) on both the scheme and the format.
        let caps = DATA_URL_RE.captures("DATA:IMAGE/JPEG;BASE64,QkJC").unwrap();
        assert_eq!(caps.name("data").unwrap().as_str(), "QkJC");
        // `jpg` is accepted alongside `png`/`jpeg`.
        assert!(DATA_URL_RE.captures("data:image/jpg;base64,Zg==").is_some());
        // DOTALL: the payload may span newlines (greedy `.+`).
        let caps = DATA_URL_RE.captures("data:image/png;base64,AA\nBB").unwrap();
        assert_eq!(caps.name("data").unwrap().as_str(), "AA\nBB");
    }

    #[test]
    fn data_url_re_rejects_non_image_or_other_format() {
        // No prefix -> no match (the handler then treats the whole string as b64).
        assert!(DATA_URL_RE.captures("AAAA").is_none());
        // A non-image mime / unsupported format does not match.
        assert!(DATA_URL_RE.captures("data:image/gif;base64,AAAA").is_none());
        assert!(DATA_URL_RE.captures("data:text/plain;base64,AAAA").is_none());
    }

    #[test]
    fn strict_base64_matches_python_validate_true() {
        // Valid standard base64 decodes.
        assert!(base64::engine::general_purpose::STANDARD
            .decode("AAAA".as_bytes())
            .is_ok());
        // `validate=True` rejects characters outside the alphabet (e.g. a space).
        assert!(base64::engine::general_purpose::STANDARD
            .decode("AA AA".as_bytes())
            .is_err());
        // url-safe alphabet (`-`/`_`) is NOT the standard alphabet -> rejected,
        // matching `b64decode(..., validate=True)`.
        assert!(base64::engine::general_purpose::STANDARD
            .decode("AA-_".as_bytes())
            .is_err());
    }

    #[test]
    fn empty_payload_is_the_400_branch() {
        // `payload = b64decode("", validate=True)` -> b"" -> `if not payload` -> 400.
        let decoded = base64::engine::general_purpose::STANDARD
            .decode("".as_bytes())
            .unwrap();
        assert!(decoded.is_empty());
    }

    #[test]
    fn name_default_and_strip_logic() {
        // `(req.name or "Signature").strip() or "Signature"`.
        let resolve = |name: Option<&str>| -> String {
            let candidate = name.map(str::to_string).unwrap_or_else(|| "Signature".to_string());
            let trimmed = candidate.trim();
            if trimmed.is_empty() {
                "Signature".to_string()
            } else {
                trimmed.to_string()
            }
        };
        assert_eq!(resolve(None), "Signature");
        assert_eq!(resolve(Some("")), "Signature");
        assert_eq!(resolve(Some("   ")), "Signature");
        assert_eq!(resolve(Some("  My Sig  ")), "My Sig");
    }

    #[test]
    fn to_dict_shape_and_key_order() {
        let d = to_dict(
            "sig-1",
            Some("My Sig"),
            "AAAA",
            Some(120),
            Some(40),
            Some("2026-06-01 12:30:00"),
        );
        assert_eq!(
            d,
            json!({
                "id": "sig-1",
                "name": "My Sig",
                "data_url": "data:image/png;base64,AAAA",
                "width": 120,
                "height": 40,
                // isoformat + "Z" (no microseconds when the stored value has none).
                "created_at": "2026-06-01T12:30:00Z",
            })
        );
        // Key order is the Python dict-literal order.
        let keys: Vec<&str> = d.as_object().unwrap().keys().map(|k| k.as_str()).collect();
        assert_eq!(
            keys,
            vec!["id", "name", "data_url", "width", "height", "created_at"]
        );
    }

    #[test]
    fn to_dict_nulls_missing_created_at_and_name() {
        let d = to_dict("sig-2", None, "ZZ", None, None, None);
        assert_eq!(d["name"], Value::Null);
        assert_eq!(d["width"], Value::Null);
        assert_eq!(d["height"], Value::Null);
        assert_eq!(d["created_at"], Value::Null);
        assert_eq!(d["data_url"], json!("data:image/png;base64,ZZ"));
    }

    #[test]
    fn iso_z_or_null_appends_z() {
        assert_eq!(iso_z_or_null(None), Value::Null);
        assert_eq!(iso_z_or_null(Some("")), Value::Null);
        // microseconds preserved when present, then Z appended.
        assert_eq!(
            iso_z_or_null(Some("2026-06-01 12:30:00.123456")),
            json!("2026-06-01T12:30:00.123456Z")
        );
    }

    #[test]
    fn signature_create_deserializes_with_optional_fields() {
        // Only `data` is required.
        let req: SignatureCreate =
            serde_json::from_value(json!({"data": "AAAA"})).unwrap();
        assert_eq!(req.data, "AAAA");
        assert!(req.name.is_none());
        assert!(req.width.is_none());
        assert!(req.svg.is_none());

        let req2: SignatureCreate = serde_json::from_value(json!({
            "data": "QkJC",
            "name": "Sig",
            "width": 200,
            "height": 80,
            "svg": "<svg/>"
        }))
        .unwrap();
        assert_eq!(req2.name.as_deref(), Some("Sig"));
        assert_eq!(req2.width, Some(200));
        assert_eq!(req2.height, Some(80));
        assert_eq!(req2.svg.as_deref(), Some("<svg/>"));
    }

    #[test]
    fn encrypt_round_trips_through_decrypt() {
        // The EncryptedText write/read pair the handlers depend on: what we store
        // for `data_png` decrypts back to the plaintext b64 the caller sent.
        let b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";
        let enc = crate::src::secret_storage::encrypt(b64);
        assert_eq!(crate::src::secret_storage::decrypt(&enc), b64);
    }
}
