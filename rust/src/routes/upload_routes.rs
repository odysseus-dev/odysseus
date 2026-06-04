// routes/upload_routes.rs  <- routes/upload_routes.py
//! File-upload API routes (routes WAVE 2).
//!
//! Faithful translation of `routes/upload_routes.py`'s `setup_upload_routes`
//! factory. Six handlers on the `/api/upload` prefix plus the standalone
//! `periodic_rate_limit_cleanup` background coroutine:
//!
//!   * `POST /api/upload`              — multipart file upload (per-IP burst cap)
//!   * `POST /api/upload/cleanup`      — admin-gated manual cleanup
//!   * `GET  /api/upload/stats`        — admin-gated upload statistics
//!   * `GET  /api/upload/{file_id}`    — download a file (FileResponse-equivalent)
//!   * `GET  /api/upload/{file_id}/vision` — vision OCR/description (cached)
//!   * `PUT  /api/upload/{file_id}/vision` — persist an edited vision text
//!
//! ## Shape (the integration substrate, mirroring the proof batch)
//! * `setup_upload_routes(upload_handler)` -> the Python factory returns
//!   `(router, periodic_rate_limit_cleanup)`. The Rust [`setup_upload_routes`]
//!   returns the `Router<AppState>`; the `upload_handler` factory arg is reached
//!   through `AppState.upload_handler`, so the factory takes no params. The
//!   `periodic_rate_limit_cleanup` background task is hoisted to a standalone
//!   `pub async fn` ([`periodic_rate_limit_cleanup`]) so the Integrate step can
//!   spawn it in `run()` (this module does NOT touch `run()`).
//! * `APIRouter(prefix="/api/upload")` -> each route mounted at the absolute
//!   `/api/upload[...]` path. axum 0.7 dynamic captures use the COLON form
//!   (`/api/upload/:file_id`), matching `{file_id}` in the Python paths.
//! * `raise HTTPException(s, d)` -> `return Err(HttpException::new(s, d))`.
//! * `require_admin(request)` -> the F3 [`auth_adapter::require_admin`] (reads the
//!   `X-Odysseus-Internal-Token` header + the stamped `CurrentUser`).
//! * `get_current_user(request)` -> `Option<Extension<CurrentUser>>` mapped to
//!   `Option<String>` (the `None` auth-disabled / unresolved case is load-bearing;
//!   the owner check is skipped entirely when `auth_manager` is not configured).
//! * `FileResponse(path, media_type=mime, filename=name)` -> a streamed byte body
//!   with `Content-Type: <mime>` and `Content-Disposition: attachment; filename=...`,
//!   the headers Starlette's `FileResponse` emits for a download.
//!
//! ## `?thumb=1` PIL thumbnail (WIRED — `image` crate)
//! The Python `?thumb=1` branch generates a cached 320px JPEG thumbnail via PIL
//! (`Image.open` + `ImageOps.exif_transpose` + `Image.thumbnail` + `save`). This
//! is now a faithful port using the already-present `image` 0.25 crate
//! ([`make_thumbnail`]): decode the source, apply the EXIF orientation
//! (`DynamicImage::apply_orientation`, the `ImageOps.exif_transpose` analogue),
//! aspect-preserving downscale that NEVER enlarges (PIL `Image.thumbnail`
//! semantics — `image::DynamicImage::thumbnail()` upscales, so it is deliberately
//! NOT used; instead a guarded `resize` with a `min(320/w, 320/h, 1.0)` factor),
//! convert to RGB when not already RGB/Luma (`convert("RGB")`), and re-encode as
//! JPEG quality 80, cached at `UPLOAD_DIR/.thumbs/<file_id>.jpg` with the same
//! mtime-staleness regen check. The whole block stays wrapped exactly like the
//! Python `try/except`: on ANY failure it logs the EXACT warning
//! `Thumbnail generation failed for {file_id}: {e}` and falls through to the full
//! image — never a fabricated thumbnail. See the `?thumb=1` call site.
//!
//! ## No path collision
//! Every route is under `/api/upload[...]`, a prefix the inline `web/mod.rs`
//! subset never owns, so the aggregator merges this router without an axum
//! duplicate-`method`+`path` panic.


use std::fs::File;
use std::io::{BufReader, BufWriter};
use std::time::Duration;

use axum::body::Body;
use axum::extract::{ConnectInfo, Multipart, Path, Query, State};
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Extension, Json, Router};
use serde::Deserialize;
use serde_json::{json, Map, Value};
use std::net::SocketAddr;

use crate::core::middleware::INTERNAL_TOOL_HEADER;
use crate::pylog as logger;
use crate::routes::auth_adapter;
use crate::routes::{AppState, CurrentUser, HttpException};
use crate::src::constants::UPLOAD_DIR;

/// `setup_upload_routes(upload_handler)` — assemble the upload router.
///
/// The Python registers, in this order:
/// `POST ""`, `POST "/cleanup"`, `GET "/stats"`, `GET "/{file_id}"`,
/// `GET "/{file_id}/vision"`, `PUT "/{file_id}/vision"`. Under the
/// `prefix="/api/upload"` those resolve to the absolute paths below. axum matches
/// static segments (`/cleanup`, `/stats`) and the longer `/:file_id/vision` ahead
/// of the bare `/:file_id` capture regardless of registration order, so there is
/// no ambiguity; we keep the Python source order for fidelity.
///
/// The Python factory returns `(router, periodic_rate_limit_cleanup)`. Here the
/// background task is the standalone [`periodic_rate_limit_cleanup`] (spawned by
/// `run()` in the Integrate step), so this returns just the `Router<AppState>`.
pub fn setup_upload_routes() -> Router<AppState> {
    Router::new()
        // `@router.post("")` — the prefix itself: `POST /api/upload`.
        .route("/api/upload", post(api_upload))
        // `@router.post("/cleanup")`
        .route("/api/upload/cleanup", post(manual_cleanup))
        // `@router.get("/stats")`
        .route("/api/upload/stats", get(upload_stats))
        // `@router.get("/{file_id}")`
        .route("/api/upload/:file_id", get(download_file))
        // `@router.get("/{file_id}/vision")` + `@router.put("/{file_id}/vision")`
        .route(
            "/api/upload/:file_id/vision",
            get(get_vision_text).put(put_vision_text),
        )
}

/// `POST /api/upload` — upload files with enhanced security and organization.
///
/// ```python
/// @router.post("")
/// async def api_upload(request: Request, files: List[UploadFile] = File(...)):
///     if not files: raise HTTPException(400, "No files uploaded")
///     client_ip = request.client.host if request.client else "unknown"
///     ...
/// ```
async fn api_upload(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    connect_info: Option<ConnectInfo<SocketAddr>>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    let upload_handler = s.upload_handler.as_ref();

    // `files: List[UploadFile] = File(...)` — read all multipart parts named
    // `files`, each yielding (filename, bytes). FastAPI binds the repeated
    // `files` field to the list; we collect them in body order.
    let files = read_files(mp).await;

    // `if not files: raise HTTPException(400, "No files uploaded")`. FastAPI's
    // `File(...)` is required (a totally empty body is its 422 validation error),
    // but the explicit `if not files` 400 fires when the multipart parsed to an
    // empty list — reproduce the Python's own 400 here for the empty case.
    if files.is_empty() {
        return Err(HttpException::new(400, "No files uploaded"));
    }

    // `client_ip = request.client.host if request.client else "unknown"`.
    let client_ip = connect_info
        .map(|ConnectInfo(addr)| addr.ip().to_string())
        .unwrap_or_else(|| "unknown".to_string());

    // `owner=get_current_user(request)` — the username, or `None` when auth is
    // disabled / unresolved.
    let owner: Option<String> = user.map(|Extension(CurrentUser(u))| u);

    // --- Limit concurrent uploads per IP -----------------------------------
    // Count the per-IP upload timestamps within the past 10 seconds, INDEPENDENT
    // of how many files are in this batch (#1346 — a single >3-file batch must
    // not self-reject). The handler reads the lock-guarded `upload_rate_log` (see
    // `UploadHandler::count_recent_uploads`).
    let ip_upload_count = upload_handler.count_recent_uploads(&client_ip);
    let max_concurrent = upload_handler.max_concurrent_uploads() as usize;
    if ip_upload_count >= max_concurrent {
        // `raise HTTPException(429, f"Maximum concurrent uploads ({...}) exceeded")`
        return Err(HttpException::new(
            429,
            format!("Maximum concurrent uploads ({max_concurrent}) exceeded"),
        ));
    }

    let mut out: Vec<Value> = Vec::new();
    // `for u in files:`
    for (filename, content) in &files {
        // `meta = upload_handler.save_upload(u, client_ip, owner=...)`.
        // The Rust `save_upload` takes the bytes + the original filename (the
        // FastAPI `UploadFile` is not portable). It raises an `HttpException` on
        // size/type/rate failures.
        match upload_handler.save_upload(content, filename.as_deref(), &client_ip, owner.as_deref())
        {
            Ok(meta) => {
                let g = |k: &str| meta.get(k).cloned().unwrap_or(Value::Null);
                out.push(json!({
                    "id": g("id"),
                    "name": g("name"),
                    "mime": g("mime"),
                    "size": g("size"),
                    "hash": g("hash"),
                    "uploaded_at": g("uploaded_at"),
                    // `meta.get("width")` / `meta.get("height")` — absent -> null.
                    "width": g("width"),
                    "height": g("height"),
                    // `meta.get("is_duplicate", False)`.
                    "is_duplicate": meta
                        .get("is_duplicate")
                        .and_then(Value::as_bool)
                        .unwrap_or(false),
                }));
            }
            // `except HTTPException: raise` — a save_upload HTTPException
            // (size/type/rate-limit) propagates straight out, aborting the loop.
            Err(e) => return Err(e),
        }
        // NOTE: the Python's `except Exception as e: logger.error(...); continue`
        // skips a non-HTTP failure for one file. `save_upload` in the Rust port
        // returns its failures as `HttpException` (the `Err` arm above) and cannot
        // otherwise raise a non-HTTP error, so there is no separate broad-`except`
        // continue path to reproduce — every failure mode is the HTTPException one.
    }

    // `if not out: raise HTTPException(500, "All file uploads failed")`. With the
    // propagate-on-error semantics above this is unreachable when `files` is
    // non-empty (any failure already returned), but kept for structural fidelity.
    if out.is_empty() {
        return Err(HttpException::new(500, "All file uploads failed"));
    }

    // `return {"files": out}`.
    Ok(Json(json!({ "files": out })).into_response())
}

/// `POST /api/upload/cleanup` — manually trigger cleanup of old uploads.
///
/// ```python
/// @router.post("/cleanup")
/// async def manual_cleanup(request: Request):
///     require_admin(request)
///     cleaned_count = upload_handler.cleanup_old_uploads()
///     return {"status": "success", "files_cleaned": cleaned_count}
/// ```
async fn manual_cleanup(
    State(s): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    admin_gate(&s, &headers, user.as_deref())?;
    let cleaned_count = s.upload_handler.cleanup_old_uploads();
    Ok(Json(json!({"status": "success", "files_cleaned": cleaned_count})).into_response())
}

/// `GET /api/upload/stats` — get statistics about uploaded files.
///
/// ```python
/// @router.get("/stats")
/// async def upload_stats(request: Request):
///     require_admin(request)
///     try: return upload_handler.get_upload_stats()
///     except Exception as e:
///         logger.error(...); raise HTTPException(500, "Failed to get upload statistics")
/// ```
async fn upload_stats(
    State(s): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    admin_gate(&s, &headers, user.as_deref())?;
    // `get_upload_stats()` in the Rust port already swallows its own IO/JSON
    // errors internally (returning `{"error": <msg>}` rather than raising), so the
    // route-level 500 wrapper is unreachable on the live path — exactly like the
    // Python, where a failing inner read would propagate, but the realistic path
    // returns the stats dict.
    let stats = s.upload_handler.get_upload_stats();
    Ok(Json(Value::Object(stats)).into_response())
}

/// `thumb: int = 0` / `force: int = 0` query parameter.
///
/// FastAPI coerces the `thumb`/`force` query value to an `int` (default `0`); the
/// handler then treats it as truthy (`if thumb` / `if not force`). We parse it as
/// an integer with a `0` default so `?thumb=1` is truthy and an absent/`0` value
/// is falsy, matching the Python `int` semantics.
#[derive(Debug, Deserialize)]
struct ThumbQuery {
    #[serde(default)]
    thumb: i64,
}

#[derive(Debug, Deserialize)]
struct ForceQuery {
    #[serde(default)]
    force: i64,
}

/// `GET /api/upload/{file_id}` — serve an uploaded file by its ID.
///
/// `?thumb=1` returns a small cached 320px JPEG thumbnail for images (generated
/// via the `image` crate; see [`make_thumbnail`] and the call site below). On any
/// thumbnail failure the handler logs the Python warning and serves the full
/// image, exactly like the Python `try/except` fall-through.
async fn download_file(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(file_id): Path<String>,
    Query(q): Query<ThumbQuery>,
) -> Result<Response, HttpException> {
    let upload_handler = s.upload_handler.as_ref();

    // `if not upload_handler.validate_upload_id(file_id): raise HTTPException(400, ...)`
    if !upload_handler.validate_upload_id(&file_id) {
        return Err(HttpException::new(400, "Invalid file ID"));
    }

    // Resolve the on-disk path (search the upload dirs).
    // `path = os.path.join(UPLOAD_DIR, file_id)` then walk if missing.
    let path = match resolve_upload_path(&file_id) {
        Some(p) => p,
        // `else: raise HTTPException(404, "File not found")`
        None => return Err(HttpException::new(404, "File not found")),
    };

    // `if not upload_handler.inside_base_dir(path): raise HTTPException(403, ...)`
    if !upload_handler.inside_base_dir(&path) {
        return Err(HttpException::new(403, "Access denied"));
    }

    // Look up original filename and owner from uploads.json.
    // `original_name = file_id; info = next((fi for fi in db.values() if fi["id"] == file_id), None)`
    let info = load_upload_info(&file_id);
    let original_name = info
        .as_ref()
        .and_then(|i| i.get("name").and_then(Value::as_str))
        .unwrap_or(&file_id)
        .to_string();

    // Owner / auth gate.
    let file_owner = info.as_ref().and_then(|i| i.get("owner").and_then(Value::as_str));
    enforce_owner_access(&s, user.as_deref(), file_owner)?;

    // `mime = mimetypes.guess_type(path)[0] or "application/octet-stream"`.
    let mime = mime_guess::from_path(&path)
        .first_raw()
        .unwrap_or("application/octet-stream");

    // --- `?thumb=1` image thumbnail ----------------------------------------
    // ```python
    // if thumb and mime.startswith("image/"):
    //     try:
    //         ... # build/cache a 320px JPEG via PIL, then
    //         return FileResponse(thumb_path, media_type="image/jpeg")
    //     except Exception as e:
    //         logger.warning(f"Thumbnail generation failed for {file_id}: {e}")
    //         # Fall through to the full image.
    // ```
    // The whole block is wrapped so ANY failure logs the EXACT Python warning and
    // falls through to the full-image response below — faithful to the Python's
    // own `try/except` fall-through. On success the thumbnail is served as
    // `image/jpeg` with NO filename (Starlette `FileResponse(..., media_type=...)`
    // here passes no `filename`).
    if q.thumb != 0 && mime.starts_with("image/") {
        match make_thumbnail(&path, &file_id) {
            // `return FileResponse(thumb_path, media_type="image/jpeg")`.
            Ok(thumb_path) => return file_response(&thumb_path, "image/jpeg", None),
            // `except Exception as e: logger.warning(...)` then fall through.
            Err(e) => {
                logger::warning(&format!("Thumbnail generation failed for {file_id}: {e}"));
                // Fall through to the full image.
            }
        }
    }

    // `return FileResponse(path, media_type=mime, filename=original_name)`.
    file_response(&path, mime, Some(&original_name))
}

/// `GET /api/upload/{file_id}/vision` — vision-model OCR/description for an image.
///
/// Cached under `UPLOAD_DIR/.vision/{file_id}.txt`; `force=1` recomputes.
async fn get_vision_text(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(file_id): Path<String>,
    Query(q): Query<ForceQuery>,
) -> Result<Response, HttpException> {
    let upload_handler = s.upload_handler.as_ref();

    // `if not upload_handler.validate_upload_id(file_id): raise HTTPException(400, ...)`
    if !upload_handler.validate_upload_id(&file_id) {
        return Err(HttpException::new(400, "Invalid file ID"));
    }

    // Resolve path (walk the upload dirs).
    let path = match resolve_upload_path(&file_id) {
        Some(p) => p,
        None => return Err(HttpException::new(404, "File not found")),
    };
    // `if not upload_handler.inside_base_dir(path): raise HTTPException(403, ...)`
    if !upload_handler.inside_base_dir(&path) {
        return Err(HttpException::new(403, "Access denied"));
    }

    // `info = _load_upload_info(file_id)` + owner/auth gate.
    let info = load_upload_info(&file_id);
    let file_owner = info.as_ref().and_then(|i| i.get("owner").and_then(Value::as_str));
    enforce_owner_access(&s, user.as_deref(), file_owner)?;

    // `mime = mimetypes.guess_type(path)[0] or ""`.
    let mime = mime_guess::from_path(&path).first_raw().unwrap_or("");
    // `if not mime.startswith("image/"): raise HTTPException(400, "Not an image")`
    if !mime.starts_with("image/") {
        return Err(HttpException::new(400, "Not an image"));
    }

    // `cache_path = _vision_cache_path(file_id)`.
    let cache_path = vision_cache_path(&file_id);
    // `if not force and os.path.exists(cache_path): try: return {"text": ..., "cached": True}`
    if q.force == 0 && crate::pyos::path::exists(&cache_path) {
        match std::fs::read_to_string(&cache_path) {
            Ok(text) => return Ok(Json(json!({"text": text, "cached": true})).into_response()),
            // `except Exception as e: logger.warning("Vision cache read failed ...")`
            // then fall through to recompute.
            Err(e) => logger::warning(&format!("Vision cache read failed for {file_id}: {e}")),
        }
    }

    // `text = analyze_image_with_vl(path) or ""`. The ported `analyze_image_with_vl`
    // is an HONEST DEFER (vision-model resolution is not ported); it returns the
    // standard "not available" marker rather than a fabricated description, and
    // never raises — so the Python's `except Exception -> 500` branch is
    // unreachable here (kept structurally below for fidelity).
    let text = crate::src::document_processor::analyze_image_with_vl(&path);

    // `try: with open(cache_path, "w") as f: f.write(text)`.
    if let Err(e) = std::fs::write(&cache_path, &text) {
        // `except Exception as e: logger.warning("Vision cache write failed ...")`.
        logger::warning(&format!("Vision cache write failed for {file_id}: {e}"));
    }

    // `return {"text": text, "cached": False}`.
    Ok(Json(json!({"text": text, "cached": false})).into_response())
}

/// `class _VisionPutBody` — the JSON body for `PUT /api/upload/{file_id}/vision`.
///
/// `text = (body or {}).get("text", "")` — defaults to the empty string when
/// absent; `#[serde(default)]` reproduces that. `serde_json::Value` is used so a
/// non-string `text` can be detected and rejected with the same 400 the Python
/// raises (`isinstance(text, str)` check).
#[derive(Debug, Deserialize, Default)]
struct VisionPutBody {
    #[serde(default)]
    text: Value,
}

/// `PUT /api/upload/{file_id}/vision` — persist a user-edited vision/OCR text.
async fn put_vision_text(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(file_id): Path<String>,
    body: Option<Json<VisionPutBody>>,
) -> Result<Response, HttpException> {
    let upload_handler = s.upload_handler.as_ref();

    // `if not upload_handler.validate_upload_id(file_id): raise HTTPException(400, ...)`
    if !upload_handler.validate_upload_id(&file_id) {
        return Err(HttpException::new(400, "Invalid file ID"));
    }

    // `info = _load_upload_info(file_id); if not info: raise HTTPException(404, ...)`
    let info = match load_upload_info(&file_id) {
        Some(i) => i,
        None => return Err(HttpException::new(404, "File not found")),
    };

    // Owner / auth gate (`file_owner = info.get("owner")`).
    let file_owner = info.get("owner").and_then(Value::as_str);
    enforce_owner_access(&s, user.as_deref(), file_owner)?;

    // `body = await request.json(); text = (body or {}).get("text", "")`.
    let body = body.map(|Json(b)| b).unwrap_or_default();
    // `if not isinstance(text, str): raise HTTPException(400, "text must be a string")`.
    let text = match &body.text {
        // Absent -> the `#[serde(default)]` `Value::Null` stands in for the
        // Python `.get("text", "")` default; treat as the empty string.
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        _ => return Err(HttpException::new(400, "text must be a string")),
    };

    // `with open(_vision_cache_path(file_id), "w") as f: f.write(text)`.
    let cache_path = vision_cache_path(&file_id);
    if let Err(e) = std::fs::write(&cache_path, &text) {
        // The Python `open(...,"w")` would raise on a write failure, surfacing as
        // a 500 from FastAPI's default handler; mirror that.
        return Err(HttpException::new(500, e.to_string()));
    }

    // `return {"ok": True}`.
    Ok(Json(json!({"ok": true})).into_response())
}

/// `async def periodic_rate_limit_cleanup()` — background task to run cleanup
/// every hour.
///
/// ```python
/// async def periodic_rate_limit_cleanup():
///     while True:
///         await asyncio.sleep(3600)
///         upload_handler.cleanup_rate_limits()
/// ```
///
/// The Python factory returns this coroutine alongside the router; `run()` spawns
/// it. Hoisted to a standalone `pub async fn` (taking the shared [`AppState`] so
/// it reaches `upload_handler` the same way the router does) for the Integrate
/// step to `tokio::spawn`. This module does NOT edit `run()`.
pub async fn periodic_rate_limit_cleanup(state: AppState) {
    loop {
        // `await asyncio.sleep(3600)`.
        tokio::time::sleep(Duration::from_secs(3600)).await;
        // `upload_handler.cleanup_rate_limits()`.
        state.upload_handler.cleanup_rate_limits();
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// `require_admin(request)` — the per-handler admin gate.
///
/// Reads the `X-Odysseus-Internal-Token` header and the stamped
/// `request.state.current_user`, then delegates to the foundation F3
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

/// Resolve the on-disk path for an upload `file_id`, searching the upload dirs.
///
/// ```python
/// path = os.path.join(UPLOAD_DIR, file_id)
/// if not os.path.exists(path):
///     for root, dirs, files in os.walk(UPLOAD_DIR):
///         if file_id in files:
///             path = os.path.join(root, file_id); break
///     else:
///         raise HTTPException(404, "File not found")
/// ```
/// Returns `Some(path)` for a found file, or `None` for the `for/else` 404 case.
fn resolve_upload_path(file_id: &str) -> Option<String> {
    // `path = os.path.join(UPLOAD_DIR, file_id)`.
    let direct = crate::pyos::path::join(&UPLOAD_DIR, file_id);
    // `if not os.path.exists(path):`
    if crate::pyos::path::exists(&direct) {
        return Some(direct);
    }
    // `for root, dirs, files in os.walk(UPLOAD_DIR):` — find a file whose basename
    // equals `file_id`. `os.walk` lists each directory's immediate files; the
    // first match (in walk order) wins (`break`). `walkdir` yields entries in a
    // deterministic order; we filter to files whose file_name matches.
    for entry in walkdir::WalkDir::new(&*UPLOAD_DIR)
        .into_iter()
        .filter_map(|e| e.ok())
    {
        if entry.file_type().is_file()
            && entry.file_name().to_string_lossy() == file_id
        {
            // `path = os.path.join(root, file_id)`.
            return Some(entry.path().to_string_lossy().into_owned());
        }
    }
    // `else: raise HTTPException(404, "File not found")`.
    None
}

/// `_load_upload_info(file_id)` — look up the uploads.json record for a file_id.
///
/// ```python
/// info = None
/// uploads_db = os.path.join(UPLOAD_DIR, "uploads.json")
/// if os.path.exists(uploads_db):
///     with open(uploads_db) as f: db = json.load(f)
///     info = next((fi for fi in db.values() if fi["id"] == file_id), None)
/// return info
/// ```
/// Returns the first record (in `db.values()` insertion order) whose `id` matches.
fn load_upload_info(file_id: &str) -> Option<Map<String, Value>> {
    let uploads_db = crate::pyos::path::join(&UPLOAD_DIR, "uploads.json");
    if !crate::pyos::path::exists(&uploads_db) {
        return None;
    }
    let raw = std::fs::read_to_string(&uploads_db).ok()?;
    let db: Value = serde_json::from_str(&raw).ok()?;
    let obj = db.as_object()?;
    // `next((fi for fi in db.values() if fi["id"] == file_id), None)` — `serde_json`
    // preserves the object's insertion order (`preserve_order`), matching Python's
    // dict iteration order so the first match is identical.
    for fi in obj.values() {
        if fi.get("id").and_then(Value::as_str) == Some(file_id) {
            return fi.as_object().cloned();
        }
    }
    None
}

/// `_vision_cache_path(file_id)` — `UPLOAD_DIR/.vision/{file_id}.txt`, dir created.
///
/// ```python
/// cache_dir = os.path.join(UPLOAD_DIR, ".vision")
/// os.makedirs(cache_dir, exist_ok=True)
/// return os.path.join(cache_dir, file_id + ".txt")
/// ```
fn vision_cache_path(file_id: &str) -> String {
    let cache_dir = crate::pyos::path::join(&UPLOAD_DIR, ".vision");
    let _ = crate::pyos::makedirs(&cache_dir, true);
    crate::pyos::path::join(&cache_dir, &format!("{file_id}.txt"))
}

/// The shared owner/auth gate used by the download + vision handlers:
///
/// ```python
/// auth_mgr = getattr(request.app.state, "auth_manager", None)
/// auth_configured = bool(auth_mgr and auth_mgr.is_configured)
/// current_user = get_current_user(request)
/// file_owner = info.get("owner") if info else None
/// if auth_configured:
///     if not current_user: raise HTTPException(403, "Access denied")
///     if file_owner != current_user and not auth_mgr.is_admin(current_user):
///         raise HTTPException(404, "File not found")
/// ```
///
/// `auth_manager` is always wired in the axum app, so `auth_configured` reduces to
/// `state.auth.is_configured()`. When auth is NOT configured the check is skipped
/// entirely (the single-user / first-run case). `current_user` is the resolved
/// username (`None` when unresolved). 403 leaks nothing (no user at all); 404 (not
/// 403) is used for a foreign-owned file so existence is not disclosed.
fn enforce_owner_access(
    state: &AppState,
    current_user: Option<&CurrentUser>,
    file_owner: Option<&str>,
) -> Result<(), HttpException> {
    // `auth_configured = bool(auth_mgr and auth_mgr.is_configured)`.
    if !state.auth.is_configured() {
        return Ok(());
    }
    // `current_user = get_current_user(request)`.
    let current_user = current_user.map(|u| u.0.as_str());
    // `if not current_user: raise HTTPException(403, "Access denied")`.
    let current_user = match current_user {
        Some(u) if !u.is_empty() => u,
        _ => return Err(HttpException::new(403, "Access denied")),
    };
    // `if file_owner != current_user and not auth_mgr.is_admin(current_user):`
    if file_owner != Some(current_user) && !state.auth.is_admin(current_user) {
        // 404 (not 403) so we don't leak existence.
        return Err(HttpException::new(404, "File not found"));
    }
    Ok(())
}

/// `FileResponse(path, media_type=mime, filename=...)` — read the file and stream
/// it with the matching `Content-Type` and (when a `filename` is given)
/// `Content-Disposition: attachment; filename="..."`, the headers Starlette's
/// `FileResponse` emits.
///
/// A read failure surfaces as a 500, the way Starlette's `FileResponse` would
/// raise (the path is verified to exist by the caller, so this is the
/// unexpected-IO case).
fn file_response(path: &str, mime: &str, filename: Option<&str>) -> Result<Response, HttpException> {
    let bytes = std::fs::read(path)
        .map_err(|e| HttpException::new(500, format!("Failed to read file: {e}")))?;
    let mut builder = Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, mime);
    if let Some(name) = filename {
        // Starlette's `FileResponse(filename=...)` sets
        // `Content-Disposition: attachment; filename="<name>"` (the stored `name`
        // is `secure_filename`-sanitised ASCII, so no `filename*` extension is
        // needed). A non-header-safe value would be dropped; `name` here is always
        // header-safe, so `.unwrap_or(builder)` keeps the response intact.
        let disposition = format!("attachment; filename=\"{name}\"");
        builder = builder.header(header::CONTENT_DISPOSITION, disposition);
    }
    builder
        .body(Body::from(bytes))
        .map_err(|e| HttpException::new(500, e.to_string()))
}

/// Build (and cache) the 320px JPEG thumbnail for an image upload, returning the
/// cached thumbnail path.
///
/// Faithful port of the Python `?thumb=1` PIL block
/// (`routes/upload_routes.py:124-148`):
///
/// ```python
/// from PIL import Image, ImageOps
/// thumb_dir = os.path.join(UPLOAD_DIR, ".thumbs")
/// os.makedirs(thumb_dir, exist_ok=True)
/// thumb_path = os.path.join(thumb_dir, file_id + ".jpg")
/// if (not os.path.exists(thumb_path)
///         or os.path.getmtime(thumb_path) < os.path.getmtime(path)):
///     im = Image.open(path)
///     im = ImageOps.exif_transpose(im)   # bake EXIF rotation into pixels
///     im.thumbnail((320, 320))           # aspect-preserving, NEVER enlarges
///     if im.mode not in ("RGB", "L"):
///         im = im.convert("RGB")
///     im.save(thumb_path, "JPEG", quality=80)
/// return FileResponse(thumb_path, media_type="image/jpeg")
/// ```
///
/// Implemented with the `image` 0.25 crate:
/// * `os.makedirs(.thumbs, exist_ok=True)` -> [`std::fs::create_dir_all`].
/// * the `getmtime` staleness check -> compare `fs::metadata(..).modified()`
///   (regenerate when the thumb is missing or older than the source).
/// * `Image.open` + `ImageOps.exif_transpose` -> decode through `ImageReader`
///   and `DynamicImage::from_decoder`, reading the decoder's EXIF
///   [`Orientation`] and applying it with [`DynamicImage::apply_orientation`]
///   (the exact `exif_transpose` analogue: it bakes the EXIF rotation into the
///   pixels).
/// * `im.thumbnail((320, 320))` -> a no-upscale aspect-preserving resize that
///   reproduces PIL's EXACT target-size math (see [`pil_thumbnail_size`]): PIL's
///   `Image.thumbnail` NEVER enlarges (returns early when `320 >= w && 320 >= h`),
///   and it does NOT use a plain `floor(dim*factor)` — it clamps the long edge to
///   320 and rounds the short edge with PIL's `round_aspect` (choose `floor` or
///   `ceil`, whichever yields the closer aspect ratio, min 1). This port matches
///   PIL byte-for-byte on the output dimensions (verified across edge cases like
///   `641x640 -> 320x320`, `700x321 -> 320x147`, where a naive floor would give a
///   `319`/`146` short edge). We `resize` with `Lanczos3` (the closest match to
///   PIL's `LANCZOS`); `image::DynamicImage::thumbnail` is deliberately NOT used
///   because it upscales.
/// * `im.convert("RGB")` for non-`RGB`/`L` modes -> `to_rgb8()` when the color
///   type is neither `Rgb8` nor `L8` (drops alpha, exactly like PIL's
///   `convert("RGB")`).
/// * `im.save(thumb_path, "JPEG", quality=80)` -> [`JpegEncoder::new_with_quality`]
///   at quality 80.
///
/// Any error (open/decode/encode/IO) is returned as `Err` so the caller logs the
/// Python warning and falls through to the full image.
fn make_thumbnail(path: &str, file_id: &str) -> Result<String, String> {
    use image::metadata::Orientation;
    use image::{DynamicImage, GenericImageView, ImageDecoder, ImageReader};

    // `thumb_dir = os.path.join(UPLOAD_DIR, ".thumbs")` +
    // `os.makedirs(thumb_dir, exist_ok=True)`.
    let thumb_dir = crate::pyos::path::join(&UPLOAD_DIR, ".thumbs");
    std::fs::create_dir_all(&thumb_dir).map_err(|e| e.to_string())?;
    // `thumb_path = os.path.join(thumb_dir, file_id + ".jpg")`.
    let thumb_path = crate::pyos::path::join(&thumb_dir, &format!("{file_id}.jpg"));

    // `if (not os.path.exists(thumb_path)
    //      or os.path.getmtime(thumb_path) < os.path.getmtime(path)):`
    // Regenerate when the cached thumb is missing OR is older than the source.
    // Any metadata/mtime read error is treated as "stale" so we regenerate (the
    // generation step then surfaces a real error if the source is unreadable).
    if !thumbnail_is_fresh(&thumb_path, path) {
        // `im = Image.open(path)` — decode through the format-guessing reader so
        // we can read the EXIF orientation off the decoder before materialising
        // the pixels.
        let file = File::open(path).map_err(|e| e.to_string())?;
        let reader = ImageReader::new(BufReader::new(file))
            .with_guessed_format()
            .map_err(|e| e.to_string())?;
        let mut decoder = reader.into_decoder().map_err(|e| e.to_string())?;
        // EXIF orientation (decoders with no EXIF return `NoTransforms`).
        let orientation: Orientation = decoder.orientation().map_err(|e| e.to_string())?;
        let mut img = DynamicImage::from_decoder(decoder).map_err(|e| e.to_string())?;
        // `im = ImageOps.exif_transpose(im)` — bake the EXIF rotation into the
        // pixels (no-op when `orientation == NoTransforms`).
        img.apply_orientation(orientation);

        // `im.thumbnail((320, 320))` — aspect-preserving, NEVER enlarges.
        let (w, h) = img.dimensions();
        if let Some((nw, nh)) = pil_thumbnail_size(w, h) {
            // A `Some` means PIL would resize to `(nw, nh)`; `None` means PIL
            // returns early (`x >= width and y >= height`) and keeps the original.
            img = img.resize(nw, nh, image::imageops::FilterType::Lanczos3);
        }

        // `if im.mode not in ("RGB", "L"): im = im.convert("RGB")` — convert
        // anything that is not already RGB or 8-bit grayscale to RGB (drops alpha,
        // matching PIL's `convert("RGB")`; `JpegEncoder` requires a JPEG-encodable
        // pixel layout anyway).
        if !matches!(
            img.color(),
            image::ColorType::Rgb8 | image::ColorType::L8
        ) {
            img = DynamicImage::ImageRgb8(img.to_rgb8());
        }

        // `im.save(thumb_path, "JPEG", quality=80)`.
        let out = File::create(&thumb_path).map_err(|e| e.to_string())?;
        let mut encoder =
            image::codecs::jpeg::JpegEncoder::new_with_quality(BufWriter::new(out), 80);
        encoder.encode_image(&img).map_err(|e| e.to_string())?;
    }

    Ok(thumb_path)
}

/// PIL `Image.thumbnail((320, 320))` target-size computation — a faithful port of
/// Pillow's `Image.Image.thumbnail` -> `preserve_aspect_ratio()` for the fixed
/// 320x320 box.
///
/// ```python
/// provided_size = (math.floor(320), math.floor(320))  # (320, 320)
/// def preserve_aspect_ratio():
///     def round_aspect(number, key):
///         return max(min(math.floor(number), math.ceil(number), key=key), 1)
///     x, y = provided_size
///     if x >= self.width and y >= self.height:
///         return None                                  # no resize -> keep original
///     aspect = self.width / self.height
///     if x / y >= aspect:
///         x = round_aspect(y * aspect, key=lambda n: abs(aspect - n / y))
///     else:
///         y = round_aspect(x / aspect, key=lambda n: 0 if n == 0 else abs(aspect - x / n))
///     return x, y
/// ```
///
/// Returns `Some((w, h))` with the exact dimensions PIL would resize to, or `None`
/// when PIL returns early (the image already fits in 320x320 — PIL NEVER enlarges,
/// so the caller keeps the original). This matches PIL's output byte-for-byte
/// including its `round_aspect` short-edge rounding (which is NOT a plain
/// `floor`/`round`; it picks `floor` or `ceil`, whichever lands closer to the
/// source aspect ratio, clamped to a minimum of 1). Verified against Pillow across
/// edge cases (e.g. `641x640 -> 320x320`, `700x321 -> 320x147`, `481x480 ->
/// 320x319`) where a naive `floor(dim*factor)` would diverge by one pixel.
fn pil_thumbnail_size(width: u32, height: u32) -> Option<(u32, u32)> {
    // `provided_size = (320, 320)`.
    const BOX: f64 = 320.0;
    let w = width as f64;
    let h = height as f64;

    // `if x >= self.width and y >= self.height: return None` — already fits, PIL
    // keeps the original (never enlarges). Also guards a zero dimension (the
    // `aspect` division below would otherwise be undefined); a 0-sized image fits
    // any box, so this returns `None` and the original is kept.
    if BOX >= w && BOX >= h {
        return None;
    }

    // PIL's `round_aspect(number, key)` = max(min(floor, ceil, key=key), 1).
    // `key` returns the |aspect error| for a candidate; `min(.., key=key)` picks
    // the candidate with the smaller error, and Python's `min` keeps the FIRST
    // argument (`floor`) on a tie — reproduced here with `<=` favouring `floor`.
    let round_aspect = |number: f64, err: &dyn Fn(f64) -> f64| -> u32 {
        let lo = number.floor();
        let hi = number.ceil();
        let pick = if err(lo) <= err(hi) { lo } else { hi };
        (pick as i64).max(1) as u32
    };

    // `aspect = self.width / self.height`.
    let aspect = w / h;
    // `x, y = provided_size` (both 320).
    let (mut x, mut y) = (BOX, BOX);
    // `if x / y >= aspect:` — width is the binding edge; clamp x to 320, round y.
    if x / y >= aspect {
        // `x = round_aspect(y * aspect, key=lambda n: abs(aspect - n / y))`.
        let target = y * aspect;
        let yy = y; // capture the box height for the error closure (`n / y`).
        x = round_aspect(target, &move |n: f64| (aspect - n / yy).abs()) as f64;
    } else {
        // `y = round_aspect(x / aspect, key=lambda n: 0 if n == 0 else abs(aspect - x / n))`.
        let target = x / aspect;
        let xx = x; // capture the box width for the error closure (`x / n`).
        y = round_aspect(target, &move |n: f64| {
            if n == 0.0 {
                0.0
            } else {
                (aspect - xx / n).abs()
            }
        }) as f64;
    }

    Some((x as u32, y as u32))
}

/// The `getmtime` staleness check from the Python thumbnail block:
///
/// ```python
/// not os.path.exists(thumb_path)
///     or os.path.getmtime(thumb_path) < os.path.getmtime(path)
/// ```
///
/// Returns `true` when the cached `thumb_path` exists AND is at least as new as
/// the source `src` (i.e. NOT stale -> reuse it). A missing thumb, or any metadata
/// read failure, yields `false` so the caller regenerates (the regeneration step
/// then surfaces a real error if the source itself is unreadable). The Python's
/// `<` comparison means an equal mtime counts as fresh (reuse), which `>=` here
/// reproduces.
fn thumbnail_is_fresh(thumb_path: &str, src: &str) -> bool {
    let thumb_mtime = match std::fs::metadata(thumb_path).and_then(|m| m.modified()) {
        Ok(t) => t,
        // Missing thumb (or unreadable) -> not fresh -> regenerate.
        Err(_) => return false,
    };
    let src_mtime = match std::fs::metadata(src).and_then(|m| m.modified()) {
        Ok(t) => t,
        // Unreadable source mtime -> regenerate (generation then reports the error).
        Err(_) => return false,
    };
    // `getmtime(thumb) < getmtime(src)` is "stale"; the negation (fresh) is
    // `thumb_mtime >= src_mtime`.
    thumb_mtime >= src_mtime
}

/// Read all `multipart/form-data` parts named `files` (FastAPI's
/// `files: List[UploadFile] = File(...)`), returning each part's `(filename,
/// bytes)` in body order.
///
/// FastAPI binds the repeated `files` field to the list; each part carries its
/// own `filename` (the `UploadFile.filename`, `None` when the part has no
/// `filename` attribute, which `save_upload` then defaults to `upload_<ts>`).
/// Parts with any other field name are ignored, matching FastAPI binding only the
/// declared `files` parameter.
async fn read_files(mut mp: Multipart) -> Vec<(Option<String>, Vec<u8>)> {
    let mut out: Vec<(Option<String>, Vec<u8>)> = Vec::new();
    while let Ok(Some(field)) = mp.next_field().await {
        if field.name() != Some("files") {
            continue;
        }
        // `u.filename` — the upload's original filename (may be absent).
        let filename = field.file_name().map(str::to_string);
        if let Ok(bytes) = field.bytes().await {
            out.push((filename, bytes.to_vec()));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::src::upload_handler::UploadHandler;

    /// Build an isolated `UploadHandler` rooted in a fresh temp dir (mirrors the
    /// upload_handler module's own test helper) so the rate-limit / save logic the
    /// route handler delegates to can be exercised without `AppState`.
    fn handler() -> UploadHandler {
        let dir = std::env::temp_dir().join(format!(
            "upload_routes_test_{}_{}",
            std::process::id(),
            uuid::Uuid::new_v4().simple()
        ));
        UploadHandler::new(dir.to_str().unwrap(), dir.to_str().unwrap())
    }

    #[test]
    fn router_mounts_all_absolute_paths() {
        // The factory yields a `Router<AppState>` mergeable into a fresh router
        // (no duplicate method+path); the upload paths are disjoint, so building
        // never panics. This pins the `setup_upload_routes() -> Router<AppState>`
        // contract the aggregator merges.
        let base: Router<AppState> = Router::new();
        let _merged: Router<AppState> = base.merge(setup_upload_routes());
    }

    #[test]
    fn thumb_query_defaults_to_zero_and_parses() {
        // Absent -> 0 (falsy); present -> the parsed int (truthy when non-zero),
        // matching FastAPI's `thumb: int = 0` / `force: int = 0`. The same serde
        // `#[serde(default)]` drives the `Query<...>` extractor at runtime; here we
        // exercise the default + parse via `serde_json` (always available).
        let q: ThumbQuery = serde_json::from_value(json!({})).unwrap();
        assert_eq!(q.thumb, 0);
        let q: ThumbQuery = serde_json::from_value(json!({"thumb": 1})).unwrap();
        assert_eq!(q.thumb, 1);
        let f: ForceQuery = serde_json::from_value(json!({"force": 2})).unwrap();
        assert_eq!(f.force, 2);
        let f: ForceQuery = serde_json::from_value(json!({})).unwrap();
        assert_eq!(f.force, 0);
    }

    #[test]
    fn vision_put_body_text_default_and_type_check() {
        // Absent `text` -> Value::Null (treated as the empty-string default).
        let b: VisionPutBody = serde_json::from_value(json!({})).unwrap();
        assert!(matches!(b.text, Value::Null));
        // A string `text` is accepted.
        let b: VisionPutBody = serde_json::from_value(json!({"text": "hi"})).unwrap();
        assert_eq!(b.text.as_str(), Some("hi"));
        // A non-string `text` deserialises into `Value` (the handler rejects it
        // with the 400 "text must be a string"), matching the Python
        // `isinstance(text, str)` guard.
        let b: VisionPutBody = serde_json::from_value(json!({"text": 5})).unwrap();
        assert!(b.text.is_number());
    }

    #[test]
    fn recent_upload_count_is_batch_independent() {
        // #1346: the per-IP recent-upload count is the number of timestamps logged
        // within the last 10s, INDEPENDENT of batch size — so a single large batch
        // can no longer self-reject. Each `save_upload` stamps exactly one timestamp.
        let h = handler();
        // No prior uploads for this IP -> count 0.
        assert_eq!(h.count_recent_uploads("5.5.5.5"), 0);
        h.save_upload(b"hello there", Some("a.txt"), "5.5.5.5", None)
            .expect("save ok");
        assert_eq!(h.count_recent_uploads("5.5.5.5"), 1);
        h.save_upload(b"again", Some("b.txt"), "5.5.5.5", None)
            .expect("save ok");
        assert_eq!(h.count_recent_uploads("5.5.5.5"), 2);
        // The default per-IP concurrent cap is 3.
        assert_eq!(h.max_concurrent_uploads(), 3);
        let _ = std::fs::remove_dir_all(h.upload_dir());
    }

    #[test]
    fn vision_cache_path_under_dot_vision() {
        // `_vision_cache_path` lives under `UPLOAD_DIR/.vision/{id}.txt`.
        let p = vision_cache_path("0123456789abcdef0123456789abcdef.png");
        assert!(p.contains("/.vision/"));
        assert!(p.ends_with("0123456789abcdef0123456789abcdef.png.txt"));
    }

    #[test]
    fn load_upload_info_missing_db_is_none() {
        // With no uploads.json present (a clean data dir may or may not have one),
        // a bogus file_id never matches, so the lookup is None.
        assert!(load_upload_info("ffffffffffffffffffffffffffffffff.zzz").is_none());
    }

    // type_complexity: faithful (input_w, input_h, expected_(w,h)) test-case tuple
    // mirroring the Pillow `thumbnail` parity fixtures 1:1.
    #[allow(clippy::type_complexity)]
    #[test]
    fn pil_thumbnail_size_matches_pillow() {
        // Byte-for-byte parity with `PIL.Image.thumbnail((320, 320))`. Each
        // expected pair was produced by running Pillow on the same dimensions;
        // the cases include ones where a naive `floor(dim*factor)` would diverge
        // from PIL's `round_aspect` short-edge rounding (e.g. 641x640, 700x321).
        let cases: &[(u32, u32, Option<(u32, u32)>)] = &[
            // Downscale, long edge clamped to 320.
            (1000, 500, Some((320, 160))),
            (200, 900, Some((71, 320))),
            (319, 500, Some((204, 320))),
            (500, 319, Some((320, 204))),
            (1, 5000, Some((1, 320))),
            (5000, 1, Some((320, 1))),
            // `round_aspect` chooses CEIL where a plain floor would be one short.
            (641, 640, Some((320, 320))),
            (700, 321, Some((320, 147))),
            // `round_aspect` chooses FLOOR.
            (323, 321, Some((320, 318))),
            (481, 480, Some((320, 319))),
            (330, 329, Some((320, 319))),
            // One edge over the box, the other within: still resizes.
            (321, 321, Some((320, 320))),
            (640, 641, Some((320, 320))),
            // Already fits in 320x320 -> PIL returns early, keep original (None).
            (100, 50, None),
            (320, 320, None),
            (319, 319, None),
            // Degenerate zero-dimension input fits any box -> None (never reached
            // on a real decoded image, which always has w >= 1 && h >= 1; this
            // just pins the no-panic guard).
            (0, 0, None),
        ];
        for &(w, h, expected) in cases {
            assert_eq!(
                pil_thumbnail_size(w, h),
                expected,
                "pil_thumbnail_size({w}, {h}) diverged from Pillow"
            );
        }
    }

    #[test]
    fn thumbnail_freshness_mtime_check() {
        // `not os.path.exists(thumb) or getmtime(thumb) < getmtime(src)`:
        // a missing thumb is never fresh; a thumb written no earlier than the
        // source is fresh (reuse). The "older thumb" stale direction is the `<`
        // boundary covered by the `>=` comparison and exercised end-to-end by the
        // route; here we pin the two std-deterministic branches.
        let dir = std::env::temp_dir().join(format!(
            "thumb_fresh_{}_{}",
            std::process::id(),
            uuid::Uuid::new_v4().simple()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        let src = dir.join("src.png");
        let thumb = dir.join("thumb.jpg");
        let src_s = src.to_str().unwrap();
        let thumb_s = thumb.to_str().unwrap();

        // Missing thumb -> not fresh (regenerate).
        std::fs::write(&src, b"src").unwrap();
        assert!(!thumbnail_is_fresh(thumb_s, src_s));

        // Thumb written after the source -> fresh (its mtime is >= the source's).
        std::fs::write(&thumb, b"thumb").unwrap();
        assert!(thumbnail_is_fresh(thumb_s, src_s));

        // A non-existent source path is also treated as "not fresh" (regenerate,
        // then the generation step surfaces the real read error).
        assert!(!thumbnail_is_fresh(thumb_s, "/nonexistent/source/file"));

        let _ = std::fs::remove_dir_all(&dir);
    }
}
