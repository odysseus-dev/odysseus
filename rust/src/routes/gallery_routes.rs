// routes/gallery_routes.rs  <- routes/gallery_routes.py
//! Gallery routes — browsable library for photos and AI-generated images (routes WAVE 6).
//!
//! Faithful translation of `routes/gallery_routes.py`'s `setup_gallery_routes`
//! factory (app.py include #22). The router has no `prefix` (FastAPI
//! `APIRouter(tags=["gallery"])`), so every handler carries its absolute
//! `/api/gallery/...` or `/api/image/...` path verbatim. axum 0.7 → colon path
//! params (`/:image_id`, `/:album_id`), never braces.
//!
//! ## Auth
//! Every handler resolves `user = get_current_user(request)` → `Option<String>`,
//! read here from `Option<Extension<CurrentUser>>`. The load-bearing `None` case
//! (auth disabled / single-user) is preserved everywhere it matters:
//! `_owner_filter`'s match-everything (SQL `"1"`, [`OwnerFilter::All`]) vs
//! `owner == user`; the upload dedupe scope; the strict 404 owner-scope on
//! get/patch/delete; the `download-zip` 401.
//!
//! The 8 image-EDIT endpoints (`ai-upscale`, `style-transfer`, `/api/image/inpaint`,
//! `/api/image/harmonize`, `/api/image/denoise`, `/api/image/upscale-local`,
//! `/api/image/remove-bg`, `/api/image/enhance-face`) additionally gate on
//! `require_privilege(request, "can_generate_images")` at their top (via
//! [`require_can_generate_images`] → `auth_adapter::require_privilege`), 403ing a
//! caller without that flag. `/api/image/sharpen` is intentionally NOT gated (no
//! gate in the Python). `inpaint`/`harmonize` further run the client-supplied
//! `_endpoint` through [`crate::src::url_safety::check_outbound_url`] (SSRF
//! hardening) — 400 "Rejected endpoint URL: …" when it fails — gated stricter by
//! `IMAGE_BLOCK_PRIVATE_IPS=true`.
//!
//! ## Bound helpers (`crate::routes::gallery_helpers`)
//! `GalleryPatch`, `GalleryImage`, `_extract_exif` (a REAL port: `image` + `kamadak-exif`),
//! `_image_to_dict`, `_owner_filter` / `OwnerFilter`, `_human_size`.
//!
//! ## Editor ops — REAL Rust ports (the 7 PIL/Real-ESRGAN/rembg/GFPGAN ops)
//! DB / CRUD / zip / reqwest-proxy paths are all direct ports. The 7 image-editing
//! ops that the Python implements with PIL / Real-ESRGAN / rembg / GFPGAN are now
//! REAL Rust, NOT honest stubs:
//!   CPU ops (pure `image`/imageops, no model, no network — run inline):
//!   * `POST /api/gallery/{id}/rotate` — `pil.rotate(-angle, expand=True)` →
//!     `image_edit::rotate_expand` (90/180/270 fast paths; expand is automatic for
//!     90/270 since the canvas swaps W/H). Re-encodes in the on-disk format (jpg
//!     q95 / lossless WebP / PNG), rewrites the file, recomputes file_hash +
//!     file_size + width/height, UPDATEs the row.
//!   * `POST /api/image/sharpen` — `ImageFilter.UnsharpMask(radius=2,
//!     percent=amount*200, threshold=3)` → `image_edit::unsharp_mask`.
//!   * `POST /api/image/inpaint` (OpenAI branch) — the SD→OpenAI alpha-mask
//!     conversion + post-edit composite are `image_edit::sd_mask_to_openai_alpha`
//!     + `composite_with_mask`; the `/v1/images/edits` multipart call is reqwest.
//!   ML ops (ort/ONNX, model downloaded to DATA_DIR/onnx_models on first use,
//!   heavy run wrapped in `tokio::task::spawn_blocking`):
//!   * `POST /api/image/remove-bg` — u2net saliency (`image_models::u2net_salient_mask`)
//!     + the full hint_mask crop→run→paste-back→alpha-multiply logic.
//!   * `POST /api/image/upscale-local` — Real-ESRGAN x4plus tiled
//!     (`image_models::realesrgan_upscale`, scale 2 or 4).
//!   * `POST /api/image/denoise` — realesr-general-x4v3 at outscale=1
//!     (`image_models::realesr_general_denoise`; the `dni_weight` two-model
//!     interpolation cannot be reproduced by a single ONNX graph — `strength` is
//!     accepted best-effort, see the handler doc).
//!   * `POST /api/image/enhance-face` — GFPGAN-512 ONNX whole-image restore
//!     (`image_models::gfpgan_restore`; the full detect/align/paste-back pipeline
//!     is reduced to a whole-image restore — see the handler doc). Falls back to
//!     the PIL ImageEnhance chain (`image_edit::pil_enhance_fallback`,
//!     `method:"pil"`) when the model can't be obtained, mirroring Python's own
//!     `except ImportError` branch.
//! GRACEFUL DEGRADATION = Python parity: when an ONNX model cannot be obtained at
//! runtime (no network on first use) or a run errors, the ML handlers return HTTP
//! 200 with `{"error": <friendly msg>}` (NEVER a 500), exactly like Python's
//! `{"error": "realesrgan not installed…"}` / `{"error": "Denoise failed: …"}`.
//! Missing-input stays `HTTPException(400)`.
//!   * `POST /api/gallery/{id}/ai-tag` — the `vision_enabled` gate IS ported
//!     (`load_settings().get("vision_enabled", True)` → the disabled `{"error":
//!     "Vision is disabled …"}` string); only the vision-model resolver
//!     `_resolve_vl_model` (→ un-ported `_resolve_model`) defers, reproducing the
//!     Python's own `ValueError`-catch `{"error": "No vision model configured …"}`.
//!   * `ai-upscale`/`style-transfer` ML — the diffusion-server resolver is the
//!     reqwest proxy; the Python's own failure path is reproduced.
//!   * upload EXIF/dims → the REAL `_extract_exif` (`image` + `kamadak-exif`) in
//!     gallery_helpers; the file-write + sha256 dedupe + DB insert proceed
//!     exactly as Python. replace's dims-refresh sits in a
//!     `try/except: pass`, so a decode failure is a no-op and the file is still
//!     written + committed.
//!
//! ## DB
//! Raw `rusqlite` over the `gallery_images`, `gallery_albums`, `sessions`, and
//! `chat_messages` tables (mirroring the SQLAlchemy `GalleryImage` /
//! `GalleryAlbum` / `Session` / `ChatMessage` ORMs). `EncryptedText` api_key on
//! `model_endpoints` decrypts via `src::secret_storage::decrypt`.
//!
//! ## No path collision
//! Every route is under `/api/gallery*` or `/api/image/*`, prefixes the inline
//! `web/mod.rs` subset never touches, so the aggregator merges without an axum
//! duplicate-`method`+`path` panic.


use std::collections::BTreeSet;

use std::net::SocketAddr;

use axum::body::Body;
use axum::extract::{ConnectInfo, Multipart, Path, Query, State};
use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post, put};
use axum::{Extension, Json, Router};
use rusqlite::{Connection, OptionalExtension};
use serde_json::{json, Map, Value};

use crate::routes::gallery_helpers::{
    GalleryImage, GalleryPatch, OwnerFilter, _extract_exif, _human_size, _image_to_dict,
    _owner_filter,
};
use crate::routes::{AppState, CurrentUser, HttpException};
// The REAL CPU + ML image-editing ops backing the 7 gallery-editor handlers
// (rotate / sharpen / inpaint-mask / remove-bg / upscale-local / denoise /
// enhance-face). `image_edit` = pure `image`/imageops CPU helpers; `image_models`
// = the ort/ONNX runners + the DATA_DIR/onnx_models download-to-cache helper.
use crate::src::{image_edit, image_models, upload_limits};

mod pyrandom;

const IMG_DIR: &str = "data/generated_images";

// Port of commit 193dc2f ("fix(uploads): bound direct upload reads").
// `GALLERY_UPLOAD_MAX_BYTES = int(os.getenv("ODYSSEUS_GALLERY_UPLOAD_MAX_BYTES", str(100 * 1024 * 1024)))`.
fn gallery_upload_max_bytes() -> usize {
    std::env::var("ODYSSEUS_GALLERY_UPLOAD_MAX_BYTES")
        .ok()
        .and_then(|v| v.parse::<usize>().ok())
        .unwrap_or(100 * 1024 * 1024)
}

// `GALLERY_TRANSFORM_UPLOAD_MAX_BYTES = int(os.getenv("ODYSSEUS_GALLERY_TRANSFORM_UPLOAD_MAX_BYTES", str(25 * 1024 * 1024)))`.
fn gallery_transform_upload_max_bytes() -> usize {
    std::env::var("ODYSSEUS_GALLERY_TRANSFORM_UPLOAD_MAX_BYTES")
        .ok()
        .and_then(|v| v.parse::<usize>().ok())
        .unwrap_or(25 * 1024 * 1024)
}

/// `setup_gallery_routes()` — assemble the gallery router in Python source order.
///
/// app.py builds the `APIRouter` and registers each `@router.<method>(path)` in
/// the order the source declares them. axum's `Router` matching is path-trie based
/// (order-independent), but we keep the source order for fidelity. Static
/// collection routes (`/api/gallery/albums`, `/api/gallery/tags`, …) are declared
/// before the `/:image_id` capture, exactly as the Python comment "must be before
/// {image_id} catch-all" notes — axum disambiguates statics from captures anyway.
pub fn setup_gallery_routes() -> Router<AppState> {
    Router::new()
        // ---- gallery photo CRUD / upload ----
        .route("/api/gallery/upload", post(gallery_upload))
        .route("/api/gallery/:image_id/replace", post(gallery_replace))
        .route("/api/gallery/:image_id/rename", post(gallery_rename))
        .route("/api/gallery/:image_id/rotate", post(gallery_rotate))
        .route("/api/gallery/ai-upscale", post(gallery_ai_upscale))
        .route("/api/gallery/style-transfer", post(gallery_style_transfer))
        .route("/api/gallery/tags", get(gallery_tags))
        .route("/api/gallery/library", get(gallery_library))
        // ---- album collection (before the {image_id} capture) ----
        .route("/api/gallery/albums", get(list_albums).post(create_album))
        .route("/api/gallery/stats", get(gallery_stats))
        .route("/api/gallery/ai-tag-batch", post(ai_tag_batch))
        // ---- bulk / cleanup endpoints (static, before the capture) ----
        .route("/api/gallery/download-zip", post(gallery_download_zip))
        .route("/api/gallery/clear-user-tags", post(clear_gallery_user_tags))
        .route("/api/gallery/clear-ai-tags", post(clear_gallery_ai_tags))
        .route("/api/gallery/dedupe-tags", post(dedupe_gallery_tags))
        // ---- per-image capture ----
        .route(
            "/api/gallery/:image_id",
            get(get_gallery_image)
                .patch(patch_gallery_image)
                .delete(delete_gallery_image),
        )
        .route("/api/gallery/:image_id/favorite", post(toggle_favorite))
        .route("/api/gallery/:image_id/ai-tag", post(ai_tag_image))
        // ---- album path-param routes ----
        .route(
            "/api/gallery/albums/:album_id",
            put(update_album).delete(delete_album),
        )
        .route("/api/gallery/albums/:album_id/add", post(add_to_album))
        .route(
            "/api/gallery/albums/:album_id/remove",
            post(remove_from_album),
        )
        // ---- image proxy / editor ops ----
        .route("/api/image/inpaint", post(inpaint_proxy))
        .route("/api/image/harmonize", post(harmonize_image))
        .route("/api/image/sharpen", post(sharpen_image))
        .route("/api/image/denoise", post(denoise_image))
        .route("/api/image/upscale-local", post(upscale_image_local))
        .route("/api/image/remove-bg", post(remove_background))
        .route("/api/image/enhance-face", post(enhance_face))
}

// ===========================================================================
// Column list + row hydration for the full `GalleryImage` (what `_image_to_dict`
// reads). The SELECT order must match `read_image`.
// ===========================================================================

const IMG_COLS: &str = "id, filename, prompt, model, size, quality, tags, ai_tags, \
     session_id, album_id, is_active, favorite, taken_at, camera_make, camera_model, \
     gps_lat, gps_lng, width, height, file_size, created_at, updated_at";

/// Hydrate a [`GalleryImage`] from a row selecting [`IMG_COLS`] in order.
fn read_image(r: &rusqlite::Row<'_>) -> rusqlite::Result<GalleryImage> {
    Ok(GalleryImage {
        id: r.get(0)?,
        filename: r.get(1)?,
        prompt: r.get(2)?,
        model: r.get(3)?,
        size: r.get(4)?,
        quality: r.get(5)?,
        tags: r.get(6)?,
        ai_tags: r.get(7)?,
        session_id: r.get(8)?,
        album_id: r.get(9)?,
        is_active: int_to_bool(r.get::<_, Option<i64>>(10)?),
        favorite: int_to_bool(r.get::<_, Option<i64>>(11)?),
        taken_at: r.get(12)?,
        camera_make: r.get(13)?,
        camera_model: r.get(14)?,
        gps_lat: r.get(15)?,
        gps_lng: r.get(16)?,
        width: r.get(17)?,
        height: r.get(18)?,
        file_size: r.get(19)?,
        created_at: r.get(20)?,
        updated_at: r.get(21)?,
    })
}

/// SQLite stores `Boolean` columns as 0/1 integers (or NULL). Map to `Option<bool>`.
fn int_to_bool(v: Option<i64>) -> Option<bool> {
    v.map(|n| n != 0)
}

// ===========================================================================
// POST /api/gallery/upload
// ===========================================================================

/// `gallery_upload(request)` — upload an image file with EXIF extraction + dedup.
///
/// PORT_NOW: the multipart `file`, sha256 dedupe (scoped to the user when one
/// resolves), file write under `data/generated_images`, and the DB insert all
/// translate directly. EXIF uses the stubbed [`_extract_exif`] (PIL-absent
/// branch → `{width:None, height:None, exif_error:"No module named 'PIL'"}`),
/// exactly what Python with PIL absent stores.
async fn gallery_upload(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);

    // `form = await request.form(); file = form.get("file")`. We collect the
    // `file` part (with its filename) + the `album_id` text field.
    // commit 193dc2f: `content = await read_upload_limited(file, GALLERY_UPLOAD_MAX_BYTES, "Gallery upload")`.
    let form = MultipartForm::collect_bounded(mp, gallery_upload_max_bytes(), "Gallery upload").await?;
    let file = form.file("file");
    // `if not file or not hasattr(file, 'filename'): raise HTTPException(400, ...)`.
    let (orig_filename, content) = match file {
        Some((Some(fname), bytes)) => (fname, bytes),
        _ => return Err(HttpException::new(400, "No file provided")),
    };

    // `album_id = form.get("album_id") or None` — Python truthiness: ""/absent → None.
    let album_id = non_empty(form.text("album_id"));

    // `file_hash = hashlib.sha256(content).hexdigest()`.
    let file_hash = sha256_hex(&content);

    let conn = session_local()?;

    // Duplicate detection scoped to THIS user (the security note in the Python).
    let dup: Option<(Option<String>, Option<String>)> = {
        let (sql, owner_bind) = if user.is_some() {
            (
                "SELECT id, filename FROM gallery_images \
                 WHERE file_hash = ?1 AND is_active = 1 AND owner = ?2 LIMIT 1",
                user.clone(),
            )
        } else {
            (
                "SELECT id, filename FROM gallery_images \
                 WHERE file_hash = ?1 AND is_active = 1 LIMIT 1",
                None,
            )
        };
        match owner_bind {
            Some(u) => conn
                .query_row(sql, rusqlite::params![file_hash, u], |r| {
                    Ok((r.get(0)?, r.get(1)?))
                })
                .optional()
                .map_err(db_500)?,
            None => conn
                .query_row(sql, rusqlite::params![file_hash], |r| {
                    Ok((r.get(0)?, r.get(1)?))
                })
                .optional()
                .map_err(db_500)?,
        }
    };
    if let Some((id, filename)) = dup {
        // `return {"ok": False, "duplicate": True, "filename": ..., "id": ..., "message": ...}`.
        return Ok(Json(json!({
            "ok": false,
            "duplicate": true,
            "filename": filename,
            "id": id,
            "message": "Duplicate photo skipped",
        }))
        .into_response());
    }

    // `img_dir.mkdir(parents=True, exist_ok=True)`.
    if let Err(e) = std::fs::create_dir_all(IMG_DIR) {
        return Err(HttpException::new(500, e.to_string()));
    }

    // `ext = file.filename.rsplit(".",1)[-1].lower() if "." in file.filename else "png"`.
    let ext = if orig_filename.contains('.') {
        // rsplit(".", 1)[-1] — the segment after the last dot.
        orig_filename
            .rsplit_once('.')
            .map(|(_, e)| e)
            .unwrap_or("png")
            .to_lowercase()
    } else {
        "png".to_string()
    };
    const VIDEO_EXTS: &[&str] = &["mp4", "mov", "webm", "mkv", "m4v"];
    const IMAGE_EXTS: &[&str] = &["png", "jpg", "jpeg", "webp", "gif"];
    let is_video = VIDEO_EXTS.contains(&ext.as_str());
    let is_image = IMAGE_EXTS.contains(&ext.as_str());
    if !is_video && !is_image {
        return Err(HttpException::new(
            400,
            format!("Unsupported file type: .{ext}"),
        ));
    }

    // `filename = f"{uuid.uuid4().hex[:12]}.{ext}"`.
    let hex = uuid::Uuid::new_v4().simple().to_string();
    let filename = format!("{}.{ext}", &hex[..12]);
    let img_path = std::path::Path::new(IMG_DIR).join(&filename);
    if let Err(e) = std::fs::write(&img_path, &content) {
        return Err(HttpException::new(500, e.to_string()));
    }

    // `exif = {} if is_video else _extract_exif(content)`.
    let exif: Map<String, Value> = if is_video {
        Map::new()
    } else {
        _extract_exif(&content).into_iter().collect()
    };

    // `original_name = file.filename.rsplit(".",1)[0] if "." in else file.filename`.
    let original_name = if orig_filename.contains('.') {
        // rsplit(".",1)[0] == everything before the LAST dot.
        match orig_filename.rsplit_once('.') {
            Some((stem, _)) => stem.to_string(),
            None => orig_filename.clone(),
        }
    } else {
        orig_filename.clone()
    };

    let img_id = uuid::Uuid::new_v4().to_string();
    let now = crate::pydatetime::utcnow_naive_iso();

    // `db.add(GalleryImage(...)); db.commit()`. The EXIF keys come back via `.get(...)`
    // (None on the PIL-absent path). `model="imported"`, `prompt=original_name`.
    let exif_str = |k: &str| -> Option<String> {
        exif.get(k).and_then(|v| v.as_str()).map(str::to_string)
    };
    let exif_int = |k: &str| -> Option<i64> { exif.get(k).and_then(|v| v.as_i64()) };

    if let Err(e) = conn.execute(
        "INSERT INTO gallery_images \
           (id, filename, prompt, model, owner, file_hash, file_size, width, height, \
            taken_at, camera_make, camera_model, gps_lat, gps_lng, album_id, \
            is_active, favorite, created_at, updated_at) \
         VALUES (?1, ?2, ?3, 'imported', ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, 1, 0, ?15, ?15)",
        rusqlite::params![
            img_id,
            filename,
            original_name,
            user,
            file_hash,
            content.len() as i64,
            exif_int("width"),
            exif_int("height"),
            exif_str("taken_at"),
            exif_str("camera_make"),
            exif_str("camera_model"),
            exif_str("gps_lat"),
            exif_str("gps_lng"),
            album_id,
            now,
        ],
    ) {
        return Err(HttpException::new(500, e.to_string()));
    }

    // `resp = {"ok": True, "filename": filename, "id": img_id}`; add `exif_warning`
    // when `exif.get("exif_error")` is truthy (it IS on the PIL-absent path).
    let mut resp = Map::new();
    resp.insert("ok".to_string(), json!(true));
    resp.insert("filename".to_string(), json!(filename));
    resp.insert("id".to_string(), json!(img_id));
    if let Some(err) = exif.get("exif_error").filter(|v| json_truthy(v)) {
        resp.insert("exif_warning".to_string(), err.clone());
    }
    Ok(Json(Value::Object(resp)).into_response())
}

// ===========================================================================
// POST /api/gallery/{image_id}/replace
// ===========================================================================

/// `gallery_replace(request, image_id)` — replace an image's file on disk.
///
/// PORT_NOW. The PIL dims-refresh (lines 130-137 Python) is wrapped in
/// `try/except Exception: pass`, so PIL-absent is a no-op — the file is still
/// written and the row committed with its existing `width`/`height` unchanged.
async fn gallery_replace(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(image_id): Path<String>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);
    let conn = session_local()?;

    // `img = db.query(GalleryImage).filter(id == image_id).first()`.
    let img: Option<GalleryImage> = conn
        .query_row(
            &format!("SELECT {IMG_COLS} FROM gallery_images WHERE id = ?1"),
            rusqlite::params![image_id],
            read_image,
        )
        .optional()
        .map_err(db_500)?;
    let img = match img {
        Some(i) => i,
        None => return Err(HttpException::new(404, "Image not found")),
    };
    // `if not user or img.owner != user: raise HTTPException(403, "Not your image")`.
    if !owns_image(&conn, &image_id, user.as_deref())? {
        return Err(HttpException::new(403, "Not your image"));
    }

    // `form = await request.form(); file = form.get("image")`.
    // commit 193dc2f: `content = await read_upload_limited(file, GALLERY_UPLOAD_MAX_BYTES, "Gallery replacement")`.
    let form = MultipartForm::collect_bounded(mp, gallery_upload_max_bytes(), "Gallery replacement").await?;
    let file = form.file("image");
    // `if not file or not hasattr(file, 'read'): raise HTTPException(400, "No image provided")`.
    let content = match file {
        Some((_fname, bytes)) => bytes,
        None => return Err(HttpException::new(400, "No image provided")),
    };

    if let Err(e) = std::fs::create_dir_all(IMG_DIR) {
        return Err(HttpException::new(500, e.to_string()));
    }
    // `img_path = img_dir / _sanitize_gallery_filename(img.filename)` — sanitize the
    // DB-stored filename before joining it under generated_images, blocking path
    // traversal (`..`, leading `/`) from a tampered row (gallery_routes.py:136).
    let filename = sanitize_gallery_filename(&img.filename.clone().unwrap_or_default());
    let img_path = std::path::Path::new(IMG_DIR).join(&filename);
    if let Err(e) = std::fs::write(&img_path, &content) {
        return Err(HttpException::new(500, e.to_string()));
    }

    // PIL dims-refresh is `try/except: pass` — PIL-absent → no-op. width/height
    // stay whatever the row already had (we still bump updated_at via the commit).
    let now = crate::pydatetime::utcnow_naive_iso();
    if let Err(_e) = conn.execute(
        "UPDATE gallery_images SET updated_at = ?1 WHERE id = ?2",
        rusqlite::params![now, image_id],
    ) {
        // `except Exception as e: db.rollback(); raise HTTPException(500, f"DB commit failed: {e}")`.
        return Err(HttpException::new(
            500,
            format!("DB commit failed: {_e}"),
        ));
    }

    Ok(Json(json!({ "ok": true, "width": img.width, "height": img.height })).into_response())
}

// ===========================================================================
// POST /api/gallery/{image_id}/rename
// ===========================================================================

/// `gallery_rename(request, image_id)` — store the new name in the `prompt` column.
async fn gallery_rename(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(image_id): Path<String>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);
    let data: Value = serde_json::from_slice(&raw_body).unwrap_or(Value::Null);

    // `new_name = (data.get("name") or "").strip()`.
    let new_name = data
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if new_name.is_empty() {
        return Err(HttpException::new(400, "Name cannot be empty"));
    }
    // `if len(new_name) > 500:` — Python `len` is code points.
    if new_name.chars().count() > 500 {
        return Err(HttpException::new(400, "Name too long"));
    }

    let conn = session_local()?;
    if !image_exists(&conn, &image_id)? {
        return Err(HttpException::new(404, "Image not found"));
    }
    if !owns_image(&conn, &image_id, user.as_deref())? {
        return Err(HttpException::new(403, "Not your image"));
    }
    conn.execute(
        "UPDATE gallery_images SET prompt = ?1 WHERE id = ?2",
        rusqlite::params![new_name, image_id],
    )
    .map_err(db_500)?;
    Ok(Json(json!({ "ok": true, "name": new_name })).into_response())
}

// ===========================================================================
// POST /api/gallery/{image_id}/rotate  — REAL rotate (image crate)
// ===========================================================================

/// `gallery_rotate(request, image_id)` — rotate the on-disk image ±90°/180°/270°.
///
/// Faithful port of `gallery_routes.py:173-228`. Python validates `angle =
/// int(data.get("angle", 90))` (400 "Invalid angle" on a non-int, 400 "Angle must
/// be 90, -90, 180, or 270" if not in that set), does the 404/403 owner check,
/// reads the file, `rotated = pil.rotate(-angle, expand=True)`, re-encodes in the
/// file's extension (jpg/jpeg q95, webp q95, else PNG), rewrites the file, and
/// recomputes `file_hash`/`file_size`/`width`/`height`.
///
/// PIL `rotate` is counter-clockwise, the handler negates the API angle, so the
/// net is a CLOCKWISE rotation by `angle` — `image_edit::rotate_expand` maps that
/// to `imageops::rotate90/180/270`. For 90/270 the canvas swaps W/H, which is what
/// `expand=True` produces for the right-angle cases the handler accepts.
///
/// WEBP DRIFT: PIL saves WebP lossy (quality=95); `image` 0.25's WebP encoder is
/// lossless-only. A `.webp` gallery file is re-saved as lossless WebP (visually
/// identical, larger file) — the extension/content-type contract is preserved.
///
/// `file_hash` is written via a raw UPDATE: the column exists in the schema
/// (core/database.rs:212) but is not a field on the `GalleryImage` struct.
async fn gallery_rotate(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(image_id): Path<String>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let data: Value = serde_json::from_slice(&raw_body).unwrap_or(Value::Null);

    // `angle = int(data.get("angle", 90))` — coerce; 400 "Invalid angle" on a
    // non-int. Python `int(...)` accepts an int or an int-valued string and
    // raises (Type/Value)Error otherwise; a JSON float like 90.0 also coerces.
    let angle: i64 = match data.get("angle") {
        None => 90,
        Some(Value::Number(n)) => {
            if let Some(i) = n.as_i64() {
                i
            } else if let Some(f) = n.as_f64() {
                // `int(90.0)` truncates toward zero.
                f.trunc() as i64
            } else {
                return Err(HttpException::new(400, "Invalid angle"));
            }
        }
        Some(Value::String(s)) => match s.trim().parse::<i64>() {
            Ok(i) => i,
            Err(_) => return Err(HttpException::new(400, "Invalid angle")),
        },
        // bool / null / array / object → Python `int(...)` raises (or `int(True)`
        // == 1, but the editor never sends a bool); treat anything else as invalid.
        Some(_) => return Err(HttpException::new(400, "Invalid angle")),
    };
    // `if angle not in (90, -90, 180, 270): raise HTTPException(400, ...)`.
    if !matches!(angle, 90 | -90 | 180 | 270) {
        return Err(HttpException::new(400, "Angle must be 90, -90, 180, or 270"));
    }

    let user: Option<String> = current_user(user);
    let conn = session_local()?;

    // `img = db.query(GalleryImage).filter(id == image_id).first()`; 404 then 403.
    let img: Option<GalleryImage> = conn
        .query_row(
            &format!("SELECT {IMG_COLS} FROM gallery_images WHERE id = ?1"),
            rusqlite::params![image_id],
            read_image,
        )
        .optional()
        .map_err(db_500)?;
    let img = match img {
        Some(i) => i,
        None => return Err(HttpException::new(404, "Image not found")),
    };
    if !owns_image(&conn, &image_id, user.as_deref())? {
        return Err(HttpException::new(403, "Not your image"));
    }

    // `img_path = Path("data/generated_images") / img.filename`; 404 if absent.
    let filename = img.filename.clone().unwrap_or_default();
    let img_path = std::path::Path::new(IMG_DIR).join(&filename);
    if !img_path.exists() {
        return Err(HttpException::new(404, "Image file not found"));
    }

    // Decode → rotate → re-encode in the on-disk format (matching py:209-219).
    let bytes = std::fs::read(&img_path).map_err(|e| HttpException::new(500, e.to_string()))?;
    let decoded = image::load_from_memory(&bytes)
        .map_err(|e| HttpException::new(500, format!("Failed to decode image: {e}")))?;
    let rotated = image_edit::rotate_expand(&decoded, angle);

    // `ext = img.filename.rsplit(".", 1)[-1].lower()` → format pick.
    let ext = filename.rsplit('.').next().unwrap_or("").to_lowercase();
    let content = encode_in_format(&rotated, &ext)
        .map_err(|e| HttpException::new(500, format!("Failed to encode image: {e}")))?;

    // `img_path.write_bytes(content)`.
    std::fs::write(&img_path, &content).map_err(|e| HttpException::new(500, e.to_string()))?;

    // `img.file_hash = sha256(content); img.file_size = len(content);
    //  img.width, img.height = rotated.size`.
    let file_hash = sha256_hex(&content);
    let file_size = content.len() as i64;
    let (width, height) = {
        use image::GenericImageView;
        let (w, h) = rotated.dimensions();
        (w as i64, h as i64)
    };
    conn.execute(
        "UPDATE gallery_images SET file_hash = ?1, file_size = ?2, width = ?3, height = ?4 \
         WHERE id = ?5",
        rusqlite::params![file_hash, file_size, width, height, image_id],
    )
    .map_err(db_500)?;

    // `return {"ok": True, "width": img.width, "height": img.height}`.
    Ok(Json(json!({ "ok": true, "width": width, "height": height })).into_response())
}

/// Encode a rotated `DynamicImage` in the on-disk extension, matching
/// `gallery_routes.py:209-219`: jpg/jpeg → JPEG q95, webp → WebP (lossless in
/// `image` 0.25; PIL used lossy q95 — documented drift), anything else → PNG.
fn encode_in_format(img: &image::DynamicImage, ext: &str) -> Result<Vec<u8>, String> {
    use std::io::Cursor;
    let mut buf: Vec<u8> = Vec::new();
    match ext {
        "jpg" | "jpeg" => {
            // JPEG has no alpha; PIL `Image.rotate` on an RGBA source keeps RGBA,
            // and `Image.save(JPEG)` would then raise — but the gallery's jpg files
            // decode as RGB, so this matches. Encode the RGB8 view at quality 95.
            let rgb = img.to_rgb8();
            let mut enc =
                image::codecs::jpeg::JpegEncoder::new_with_quality(&mut buf, 95);
            enc.encode(
                rgb.as_raw(),
                rgb.width(),
                rgb.height(),
                image::ExtendedColorType::Rgb8,
            )
            .map_err(|e| e.to_string())?;
        }
        "webp" => {
            // `image` 0.25 WebP encoder is lossless-only (no quality param).
            img.write_to(&mut Cursor::new(&mut buf), image::ImageFormat::WebP)
                .map_err(|e| e.to_string())?;
        }
        _ => {
            img.write_to(&mut Cursor::new(&mut buf), image::ImageFormat::Png)
                .map_err(|e| e.to_string())?;
        }
    }
    Ok(buf)
}

// ===========================================================================
// POST /api/gallery/ai-upscale  — reqwest proxy (PORT_NOW)
// ===========================================================================

/// `gallery_ai_upscale(request)` — AI upscale via the diffusion server's
/// `/v1/images/upscale`. PORT_NOW: multipart `image`, the image-endpoint DB
/// lookup, and the `httpx`→`reqwest` proxy. The broad `except Exception as e:
/// return {"error": str(e)}` is preserved (no raise on a transport error).
async fn gallery_ai_upscale(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    connect_info: Option<ConnectInfo<SocketAddr>>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    // `require_privilege(request, "can_generate_images")` (gallery_routes.py:247).
    require_can_generate_images(user, &s, connect_info)?;
    // commit 193dc2f: `image_bytes = await read_upload_limited(file, GALLERY_TRANSFORM_UPLOAD_MAX_BYTES, "Image upload")`.
    let form = MultipartForm::collect_bounded(mp, gallery_transform_upload_max_bytes(), "Image upload").await?;
    // `file = form.get("image"); if not file: raise HTTPException(400, "No image")`.
    let image_bytes = match form.file("image") {
        Some((_f, b)) => b,
        None => return Err(HttpException::new(400, "No image")),
    };
    // `scale = int(form.get("scale", "2"))`.
    let scale = form
        .text("scale")
        .and_then(|s| s.parse::<i64>().ok())
        .unwrap_or(2);
    let b64 = base64_std(&image_bytes);

    // `ep = db.query(ModelEndpoint).filter(model_type=="image", is_enabled==True).first()`.
    let ep = first_image_endpoint()?;
    let ep = match ep {
        Some(e) => e,
        None => {
            return Err(HttpException::new(
                400,
                "No image generation endpoint configured. Add one in Settings → Add Models.",
            ))
        }
    };
    let base_url = ensure_v1(ep.base_url.trim_end_matches('/'));

    // `async with httpx.AsyncClient(timeout=120) as client: resp = await client.post(...)`.
    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(120))
        .build()
    {
        Ok(c) => c,
        Err(e) => return Ok(Json(json!({ "error": e.to_string() })).into_response()),
    };
    let send = client
        .post(format!("{base_url}/images/upscale"))
        .json(&json!({ "image": b64, "scale": scale }))
        .send()
        .await;
    match send {
        Ok(resp) => {
            let status = resp.status();
            if status.as_u16() == 200 {
                let data: Value = resp.json().await.unwrap_or(Value::Null);
                // `data.get("data", [{}])[0].get("b64_json", "")`.
                let img = data
                    .get("data")
                    .and_then(|d| d.get(0))
                    .and_then(|i| i.get("b64_json"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                Ok(Json(json!({ "image": img })).into_response())
            } else {
                Ok(Json(json!({
                    "error": format!("Upscale endpoint not available ({})", status.as_u16())
                }))
                .into_response())
            }
        }
        Err(e) => Ok(Json(json!({ "error": reqwest_err(&e) })).into_response()),
    }
}

// ===========================================================================
// POST /api/gallery/style-transfer  — reqwest proxy (PORT_NOW)
// ===========================================================================

/// `gallery_style_transfer(request)` — img2img style transfer via the diffusion
/// server's `/v1/images/generations`. PORT_NOW.
async fn gallery_style_transfer(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    connect_info: Option<ConnectInfo<SocketAddr>>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    // `require_privilege(request, "can_generate_images")` (gallery_routes.py:290).
    require_can_generate_images(user, &s, connect_info)?;
    // commit 193dc2f: `image_bytes = await read_upload_limited(file, GALLERY_TRANSFORM_UPLOAD_MAX_BYTES, "Image upload")`.
    let form = MultipartForm::collect_bounded(mp, gallery_transform_upload_max_bytes(), "Image upload").await?;
    let prompt = form.text("prompt").unwrap_or_default();
    // `strength = float(form.get("strength", "0.55"))`.
    let strength = form
        .text("strength")
        .and_then(|s| s.parse::<f64>().ok())
        .unwrap_or(0.55);
    // `if not file: raise HTTPException(400, "No image")` — note: read AFTER prompt/strength.
    let image_bytes = match form.file("image") {
        Some((_f, b)) => b,
        None => return Err(HttpException::new(400, "No image")),
    };
    let b64 = base64_std(&image_bytes);

    let ep = first_image_endpoint()?;
    let ep = match ep {
        Some(e) => e,
        None => {
            return Err(HttpException::new(
                400,
                "No image generation endpoint configured.",
            ))
        }
    };
    let base_url = ensure_v1(ep.base_url.trim_end_matches('/'));

    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(180))
        .build()
    {
        Ok(c) => c,
        Err(e) => return Ok(Json(json!({ "error": e.to_string() })).into_response()),
    };
    let send = client
        .post(format!("{base_url}/images/generations"))
        .json(&json!({
            "prompt": prompt,
            "image": b64,
            "strength": strength,
            "response_format": "b64_json",
        }))
        .send()
        .await;
    match send {
        Ok(resp) => {
            let status = resp.status();
            if status.as_u16() == 200 {
                let data: Value = resp.json().await.unwrap_or(Value::Null);
                let img_data = data
                    .get("data")
                    .and_then(|d| d.get(0))
                    .and_then(|i| i.get("b64_json"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                if !img_data.is_empty() {
                    return Ok(Json(json!({ "image": img_data })).into_response());
                }
                // Falls through to the failure return when img_data is empty.
                Ok(Json(json!({
                    "error": format!("Style transfer failed ({})", status.as_u16())
                }))
                .into_response())
            } else {
                Ok(Json(json!({
                    "error": format!("Style transfer failed ({})", status.as_u16())
                }))
                .into_response())
            }
        }
        Err(e) => Ok(Json(json!({ "error": reqwest_err(&e) })).into_response()),
    }
}

// ===========================================================================
// GET /api/gallery/tags
// ===========================================================================

/// `gallery_tags(request)` — distinct tags across active images, sorted.
async fn gallery_tags(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);
    let conn = session_local()?;
    let tag_set = collect_distinct_tags(&conn, user.as_deref())?;
    // `return {"tags": sorted(tag_set)}`.
    let tags: Vec<&String> = tag_set.iter().collect();
    Ok(Json(json!({ "tags": tags })).into_response())
}

/// The shared distinct-`tags` scan used by `/tags` and `/library`:
/// ```python
/// q = db.query(GalleryImage.tags).filter(is_active==True, tags != None, tags != "")
/// q = _owner_filter(q, user)
/// for (raw,) in q.all(): for t in raw.split(","): t=t.strip(); if t: add
/// ```
/// Returns a sorted set (`BTreeSet`) so the caller's `sorted(...)` is satisfied.
fn collect_distinct_tags(
    conn: &Connection,
    user: Option<&str>,
) -> Result<BTreeSet<String>, HttpException> {
    let owner = _owner_filter(user);
    let (where_owner, bind): (&str, Option<String>) = match owner {
        // user is None -> _owner_filter returns OwnerFilter::All: SQL "1" (match
        // everything), NOT "0" — no-auth/single-user mode sees the FULL library.
        OwnerFilter::All => ("1", None),
        OwnerFilter::Owner(u) => ("owner = ?1", Some(u)),
    };
    let sql = format!(
        "SELECT tags FROM gallery_images \
         WHERE is_active = 1 AND tags IS NOT NULL AND tags != '' AND {where_owner}"
    );
    let mut stmt = conn.prepare(&sql).map_err(db_500)?;
    let mut set = BTreeSet::new();
    let mut push = |raw: Option<String>| {
        if let Some(raw) = raw {
            for t in raw.split(',') {
                let t = t.trim();
                if !t.is_empty() {
                    set.insert(t.to_string());
                }
            }
        }
    };
    match bind {
        Some(u) => {
            let mut rows = stmt
                .query(rusqlite::params![u])
                .map_err(db_500)?;
            while let Some(r) = rows.next().map_err(db_500)? {
                push(r.get(0).map_err(db_500)?);
            }
        }
        None => {
            let mut rows = stmt.query([]).map_err(db_500)?;
            while let Some(r) = rows.next().map_err(db_500)? {
                push(r.get(0).map_err(db_500)?);
            }
        }
    }
    Ok(set)
}

// ===========================================================================
// GET /api/gallery/library
// ===========================================================================

/// `gallery_library(...)` — paginated, filtered, sortable library listing.
///
/// PORT_NOW including the seeded shuffle. The whole handler is wrapped in
/// `try/except Exception` → `HTTPException(500, f"Failed to fetch gallery
/// library: {e}")`; we map a DB error to that detail.
async fn gallery_library(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<std::collections::HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);
    library_impl(user, &q).map_err(|e| {
        crate::pylog::error(&format!("Failed to fetch gallery library: {e}"));
        HttpException::new(500, format!("Failed to fetch gallery library: {e}"))
    })
}

/// The body of `gallery_library`, returning `rusqlite::Error` so the caller can
/// wrap it in the Python's `except → 500 "Failed to fetch gallery library: {e}"`.
fn library_impl(
    user: Option<String>,
    q: &std::collections::HashMap<String, String>,
) -> Result<Response, rusqlite::Error> {
    // Query params with FastAPI defaults / `ge`/`le` clamps. FastAPI would 422 on
    // an out-of-range value, but parity here favors the realistic UI input; we
    // clamp defensively (offset>=0, 1<=limit<=100) and default on parse failure.
    let search = non_empty_str(q.get("search"));
    let tag = non_empty_str(q.get("tag"));
    let model = non_empty_str(q.get("model"));
    let album = non_empty_str(q.get("album"));
    let favorites = parse_bool(q.get("favorites")).unwrap_or(false);
    let sort = q.get("sort").map(String::as_str).unwrap_or("recent");
    let seed: Option<i64> = q.get("seed").and_then(|s| s.parse::<i64>().ok());
    let offset: i64 = q.get("offset").and_then(|s| s.parse().ok()).unwrap_or(0).max(0);
    let limit: i64 = q
        .get("limit")
        .and_then(|s| s.parse().ok())
        .unwrap_or(24)
        .clamp(1, 100);

    let conn = crate::core::database::session_local()?;

    // Distinct tags + models for the filter UI (owner-scoped).
    let all_tags: Vec<String> = {
        // reuse the same scan as `/tags`; ignore the HttpException wrapper here.
        let owner = _owner_filter(user.as_deref());
        let (where_owner, bind): (&str, Option<String>) = match owner {
            // user is None -> OwnerFilter::All: SQL "1" (match everything), NOT "0".
            OwnerFilter::All => ("1", None),
            OwnerFilter::Owner(u) => ("owner = ?1", Some(u)),
        };
        let sql = format!(
            "SELECT tags FROM gallery_images \
             WHERE is_active = 1 AND tags IS NOT NULL AND tags != '' AND {where_owner}"
        );
        let mut stmt = conn.prepare(&sql)?;
        let mut set = BTreeSet::new();
        let collect = |rows: &mut rusqlite::Rows<'_>, set: &mut BTreeSet<String>| -> rusqlite::Result<()> {
            while let Some(r) = rows.next()? {
                if let Some(raw) = r.get::<_, Option<String>>(0)? {
                    for t in raw.split(',') {
                        let t = t.trim();
                        if !t.is_empty() {
                            set.insert(t.to_string());
                        }
                    }
                }
            }
            Ok(())
        };
        match bind {
            Some(u) => collect(&mut stmt.query(rusqlite::params![u])?, &mut set)?,
            None => collect(&mut stmt.query([])?, &mut set)?,
        }
        set.into_iter().collect()
    };

    let all_models: Vec<String> = {
        let owner = _owner_filter(user.as_deref());
        let (where_owner, bind): (&str, Option<String>) = match owner {
            // user is None -> OwnerFilter::All: SQL "1" (match everything), NOT "0".
            OwnerFilter::All => ("1", None),
            OwnerFilter::Owner(u) => ("owner = ?1", Some(u)),
        };
        // `model != None` filter; distinct; `sorted([m for ... if m])` (drop empties).
        let sql = format!(
            "SELECT DISTINCT model FROM gallery_images \
             WHERE is_active = 1 AND model IS NOT NULL AND {where_owner}"
        );
        let mut stmt = conn.prepare(&sql)?;
        let mut set = BTreeSet::new();
        let collect = |rows: &mut rusqlite::Rows<'_>, set: &mut BTreeSet<String>| -> rusqlite::Result<()> {
            while let Some(r) = rows.next()? {
                if let Some(m) = r.get::<_, Option<String>>(0)? {
                    if !m.is_empty() {
                        set.insert(m);
                    }
                }
            }
            Ok(())
        };
        match bind {
            Some(u) => collect(&mut stmt.query(rusqlite::params![u])?, &mut set)?,
            None => collect(&mut stmt.query([])?, &mut set)?,
        }
        set.into_iter().collect()
    };

    // Assemble the WHERE clause + bound params for the main query, in Python order.
    let mut wheres: Vec<String> = vec!["g.is_active = 1".to_string()];
    let mut binds: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();

    // user filter
    if let Some(u) = user.as_deref() {
        binds.push(Box::new(u.to_string()));
        wheres.push(format!("g.owner = ?{}", binds.len()));
    }
    // search (prompt + tags + ai_tags ilike)
    if let Some(s) = &search {
        let term = format!("%{s}%");
        binds.push(Box::new(term));
        let p = binds.len();
        wheres.push(format!(
            "(g.prompt LIKE ?{p} OR g.tags LIKE ?{p} OR g.ai_tags LIKE ?{p})"
        ));
    }
    // tag(s): each comma-separated tag adds an AND-filter (tags OR ai_tags ilike)
    if let Some(tg) = &tag {
        for one in tg.split(',') {
            let one = one.trim();
            if one.is_empty() {
                continue;
            }
            binds.push(Box::new(format!("%{one}%")));
            let p = binds.len();
            wheres.push(format!("(g.tags LIKE ?{p} OR g.ai_tags LIKE ?{p})"));
        }
    }
    // model filter
    if let Some(m) = &model {
        binds.push(Box::new(m.clone()));
        wheres.push(format!("g.model = ?{}", binds.len()));
    }
    // album filter
    if let Some(a) = &album {
        binds.push(Box::new(a.clone()));
        wheres.push(format!("g.album_id = ?{}", binds.len()));
    }
    // favorites filter
    if favorites {
        wheres.push("g.favorite = 1".to_string());
    }
    let where_sql = wheres.join(" AND ");

    let bind_refs: Vec<&dyn rusqlite::ToSql> = binds.iter().map(|p| p.as_ref()).collect();

    // total = q.count()
    let total: i64 = conn.query_row(
        &format!("SELECT COUNT(*) FROM gallery_images g WHERE {where_sql}"),
        bind_refs.as_slice(),
        |r| r.get(0),
    )?;
    // total_tagged = q.filter(ai_tags IS NOT NULL AND ai_tags != "").count()
    let total_tagged: i64 = conn.query_row(
        &format!(
            "SELECT COUNT(*) FROM gallery_images g \
             WHERE {where_sql} AND g.ai_tags IS NOT NULL AND g.ai_tags != ''"
        ),
        bind_refs.as_slice(),
        |r| r.get(0),
    )?;

    // Rows: a left join to sessions for session_name.
    let select_cols = "g.id, g.filename, g.prompt, g.model, g.size, g.quality, g.tags, g.ai_tags, \
         g.session_id, g.album_id, g.is_active, g.favorite, g.taken_at, g.camera_make, \
         g.camera_model, g.gps_lat, g.gps_lng, g.width, g.height, g.file_size, \
         g.created_at, g.updated_at, s.name"
        .to_string();

    let items: Vec<Value> = if sort == "shuffle" {
        // Seeded shuffle: fetch all matching IDs, shuffle with random.Random(seed),
        // slice the page, re-query, restore order.
        let mut all_ids: Vec<String> = {
            let id_sql = format!("SELECT g.id FROM gallery_images g WHERE {where_sql}");
            let mut stmt = conn.prepare(&id_sql)?;
            let mut rows = stmt.query(bind_refs.as_slice())?;
            let mut ids = Vec::new();
            while let Some(r) = rows.next()? {
                ids.push(r.get::<_, String>(0)?);
            }
            ids
        };
        // `rng = random.Random(seed if seed is not None else 0); rng.shuffle(all_ids)`.
        let mut rng = pyrandom::PyRandom::new(seed.unwrap_or(0));
        rng.shuffle(&mut all_ids);
        // `page_ids = all_ids[offset:offset+limit]`.
        let start = (offset as usize).min(all_ids.len());
        let end = start.saturating_add(limit as usize).min(all_ids.len());
        let page_ids = &all_ids[start..end];
        if page_ids.is_empty() {
            Vec::new()
        } else {
            // Re-query for just the page; restore shuffled order via by_id map.
            let placeholders: Vec<String> =
                (0..page_ids.len()).map(|i| format!("?{}", i + 1)).collect();
            let page_sql = format!(
                "SELECT {select_cols} FROM gallery_images g \
                 LEFT JOIN sessions s ON g.session_id = s.id \
                 WHERE g.id IN ({})",
                placeholders.join(", ")
            );
            let mut stmt = conn.prepare(&page_sql)?;
            let page_binds: Vec<&dyn rusqlite::ToSql> =
                page_ids.iter().map(|s| s as &dyn rusqlite::ToSql).collect();
            let mut rows = stmt.query(page_binds.as_slice())?;
            let mut by_id: std::collections::HashMap<String, Value> = std::collections::HashMap::new();
            while let Some(r) = rows.next()? {
                let img = read_image(r)?;
                let session_name: Option<String> = r.get(22)?;
                let id = img.id.clone().unwrap_or_default();
                by_id.insert(id, _image_to_dict(&img, session_name.as_deref()));
            }
            page_ids
                .iter()
                .filter_map(|i| by_id.remove(i))
                .collect()
        }
    } else {
        // `if sort == "oldest": order_by(created_at.asc()) else: created_at.desc()`.
        let order = if sort == "oldest" {
            "g.created_at ASC"
        } else {
            "g.created_at DESC"
        };
        let page_sql = format!(
            "SELECT {select_cols} FROM gallery_images g \
             LEFT JOIN sessions s ON g.session_id = s.id \
             WHERE {where_sql} ORDER BY {order} LIMIT {limit} OFFSET {offset}"
        );
        let mut stmt = conn.prepare(&page_sql)?;
        let mut rows = stmt.query(bind_refs.as_slice())?;
        let mut items = Vec::new();
        while let Some(r) = rows.next()? {
            let img = read_image(r)?;
            let session_name: Option<String> = r.get(22)?;
            items.push(_image_to_dict(&img, session_name.as_deref()));
        }
        items
    };

    Ok(Json(json!({
        "items": items,
        "total": total,
        "total_tagged": total_tagged,
        "tags": all_tags,
        "models": all_models,
    }))
    .into_response())
}

// ===========================================================================
// Album collection: GET/POST /api/gallery/albums
// ===========================================================================

/// `list_albums(request)` — list the caller's albums with cover + count.
async fn list_albums(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);
    let conn = session_local()?;

    // `q = db.query(GalleryAlbum); if user: q = q.filter(owner == user); order_by(created_at.desc())`.
    let (sql, bind): (&str, Option<String>) = if user.is_some() {
        (
            "SELECT id, name, description, cover_id, created_at FROM gallery_albums \
             WHERE owner = ?1 ORDER BY created_at DESC",
            user.clone(),
        )
    } else {
        (
            "SELECT id, name, description, cover_id, created_at FROM gallery_albums \
             ORDER BY created_at DESC",
            None,
        )
    };
    // One album row: (id, name, description, cover_id, created_at) — the SELECT cols.
    type AlbumRow = (String, Option<String>, Option<String>, Option<String>, Option<String>);
    let mut stmt = conn.prepare(sql).map_err(db_500)?;
    let map = |r: &rusqlite::Row<'_>| -> rusqlite::Result<AlbumRow> {
        Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?))
    };
    let albums: Vec<AlbumRow> =
        match bind {
            Some(u) => stmt
                .query_map(rusqlite::params![u], map)
                .map_err(db_500)?
                .collect::<rusqlite::Result<Vec<_>>>()
                .map_err(db_500)?,
            None => stmt
                .query_map([], map)
                .map_err(db_500)?
                .collect::<rusqlite::Result<Vec<_>>>()
                .map_err(db_500)?,
        };

    let mut result = Vec::new();
    for (id, name, description, cover_id, created_at) in albums {
        // `count = db.query(GalleryImage).filter(album_id == a.id, is_active == True).count()`.
        let count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM gallery_images WHERE album_id = ?1 AND is_active = 1",
                rusqlite::params![id],
                |r| r.get(0),
            )
            .map_err(db_500)?;
        // cover_url resolution.
        let mut cover_url: Option<String> = None;
        if let Some(cid) = cover_id.as_deref().filter(|c| !c.is_empty()) {
            let cover_fn: Option<String> = conn
                .query_row(
                    "SELECT filename FROM gallery_images WHERE id = ?1",
                    rusqlite::params![cid],
                    |r| r.get(0),
                )
                .optional()
                .map_err(db_500)?;
            if let Some(fname) = cover_fn {
                cover_url = Some(format!("/api/generated-image/{fname}"));
            }
        } else if count > 0 {
            let first_fn: Option<String> = conn
                .query_row(
                    "SELECT filename FROM gallery_images \
                     WHERE album_id = ?1 AND is_active = 1 ORDER BY created_at DESC LIMIT 1",
                    rusqlite::params![id],
                    |r| r.get(0),
                )
                .optional()
                .map_err(db_500)?;
            if let Some(fname) = first_fn {
                cover_url = Some(format!("/api/generated-image/{fname}"));
            }
        }
        result.push(json!({
            "id": id,
            "name": name,
            "description": description.unwrap_or_default(),
            "cover_url": cover_url,
            "count": count,
            "created_at": iso_or_null(created_at.as_deref()),
        }));
    }
    Ok(Json(json!({ "albums": result })).into_response())
}

/// `create_album(request)` — create a new album.
async fn create_album(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);
    let data: Value = serde_json::from_slice(&raw_body).unwrap_or(Value::Null);

    // `name = (data.get("name") or "").strip()`.
    let name = data
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();
    if name.is_empty() {
        return Err(HttpException::new(400, "Album name required"));
    }
    // `description=data.get("description", "")` (no `or` — a literal default).
    let description = data
        .get("description")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    let id = uuid::Uuid::new_v4().to_string();
    let now = crate::pydatetime::utcnow_naive_iso();
    let conn = session_local()?;
    conn.execute(
        "INSERT INTO gallery_albums (id, name, description, owner, created_at, updated_at) \
         VALUES (?1, ?2, ?3, ?4, ?5, ?5)",
        rusqlite::params![id, name, description, user, now],
    )
    .map_err(db_500)?;
    Ok(Json(json!({ "ok": true, "id": id, "name": name })).into_response())
}

// ===========================================================================
// GET /api/gallery/stats
// ===========================================================================

/// `gallery_stats(request)` — totals + human size + favorites + album count.
async fn gallery_stats(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);
    let conn = session_local()?;

    // When a user resolves, every count/sum is `AND owner = user`; else unscoped.
    let owner_clause = if user.is_some() { " AND owner = ?1" } else { "" };
    let album_owner = if user.is_some() { " WHERE owner = ?1" } else { "" };

    let q1 = |sql: String| -> Result<i64, HttpException> {
        match user.as_deref() {
            Some(u) => conn
                .query_row(&sql, rusqlite::params![u], |r| r.get(0))
                .map_err(db_500),
            None => conn.query_row(&sql, [], |r| r.get(0)).map_err(db_500),
        }
    };

    let total = q1(format!(
        "SELECT COUNT(*) FROM gallery_images WHERE is_active = 1{owner_clause}"
    ))?;
    // `func.sum(file_size)` over active; `.scalar() or 0` (NULL/None → 0).
    let total_size: i64 = match user.as_deref() {
        Some(u) => conn
            .query_row(
                &format!("SELECT COALESCE(SUM(file_size), 0) FROM gallery_images WHERE is_active = 1{owner_clause}"),
                rusqlite::params![u],
                |r| r.get(0),
            )
            .map_err(db_500)?,
        None => conn
            .query_row(
                "SELECT COALESCE(SUM(file_size), 0) FROM gallery_images WHERE is_active = 1",
                [],
                |r| r.get(0),
            )
            .map_err(db_500)?,
    };
    let fav_count = q1(format!(
        "SELECT COUNT(*) FROM gallery_images WHERE is_active = 1{owner_clause} AND favorite = 1"
    ))?;
    let album_count = q1(format!(
        "SELECT COUNT(*) FROM gallery_albums{album_owner}"
    ))?;

    Ok(Json(json!({
        "total_photos": total,
        "total_size": total_size,
        "total_size_human": _human_size(total_size),
        "favorites": fav_count,
        "albums": album_count,
    }))
    .into_response())
}

// ===========================================================================
// POST /api/gallery/ai-tag-batch
// ===========================================================================

/// `ai_tag_batch(request, album_id?, limit=200)` — queue untagged images.
///
/// PORT_NOW (DB-only — it does not actually call the vision model; it returns the
/// list of ids the client should ai-tag one-by-one).
async fn ai_tag_batch(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<std::collections::HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);
    let album_id = non_empty_str(q.get("album_id"));
    // `limit: int = Query(200)`.
    let limit: i64 = q.get("limit").and_then(|s| s.parse().ok()).unwrap_or(200);
    // `max(1, min(limit, 500))`.
    let capped = limit.clamp(1, 500);

    let conn = session_local()?;

    // WHERE: is_active AND (ai_tags IS NULL OR ai_tags = '') [AND owner] [AND album_id].
    let mut wheres = vec![
        "is_active = 1".to_string(),
        "(ai_tags IS NULL OR ai_tags = '')".to_string(),
    ];
    let mut binds: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();
    if let Some(u) = user.as_deref() {
        binds.push(Box::new(u.to_string()));
        wheres.push(format!("owner = ?{}", binds.len()));
    }
    if let Some(a) = &album_id {
        binds.push(Box::new(a.clone()));
        wheres.push(format!("album_id = ?{}", binds.len()));
    }
    let where_sql = wheres.join(" AND ");
    let bind_refs: Vec<&dyn rusqlite::ToSql> = binds.iter().map(|p| p.as_ref()).collect();

    let untagged: i64 = conn
        .query_row(
            &format!("SELECT COUNT(*) FROM gallery_images WHERE {where_sql}"),
            bind_refs.as_slice(),
            |r| r.get(0),
        )
        .map_err(db_500)?;
    // `ids = [img.id for img in q.limit(capped).all()]`.
    let mut stmt = conn
        .prepare(&format!(
            "SELECT id FROM gallery_images WHERE {where_sql} LIMIT {capped}"
        ))
        .map_err(db_500)?;
    let mut rows = stmt.query(bind_refs.as_slice()).map_err(db_500)?;
    let mut ids: Vec<String> = Vec::new();
    while let Some(r) = rows.next().map_err(db_500)? {
        ids.push(r.get::<_, String>(0).map_err(db_500)?);
    }

    Ok(Json(json!({
        "ok": true,
        "queued": ids.len(),
        "total_untagged": untagged,
        "image_ids": ids,
    }))
    .into_response())
}

// ===========================================================================
// GET /api/gallery/{image_id}
// ===========================================================================

/// `get_gallery_image(request, image_id)` — one image (owner-scoped 404).
async fn get_gallery_image(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(image_id): Path<String>,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);
    let conn = session_local()?;

    // left join sessions for session_name.
    let row: Option<(GalleryImage, Option<String>)> = conn
        .query_row(
            "SELECT g.id, g.filename, g.prompt, g.model, g.size, g.quality, g.tags, g.ai_tags, \
             g.session_id, g.album_id, g.is_active, g.favorite, g.taken_at, g.camera_make, \
             g.camera_model, g.gps_lat, g.gps_lng, g.width, g.height, g.file_size, \
             g.created_at, g.updated_at, s.name \
             FROM gallery_images g LEFT JOIN sessions s ON g.session_id = s.id \
             WHERE g.id = ?1",
            rusqlite::params![image_id],
            |r| Ok((read_image(r)?, r.get::<_, Option<String>>(22)?)),
        )
        .optional()
        .map_err(db_500)?;
    let _ = &IMG_COLS; // (the joined SELECT mirrors IMG_COLS + s.name)

    // `if not row: raise 404`. Then `if not user or img.owner != user: raise 404`.
    let (img, session_name) = match row {
        Some(r) => r,
        None => return Err(HttpException::new(404, "Image not found")),
    };
    if !owns_image(&conn, &image_id, user.as_deref())? {
        return Err(HttpException::new(404, "Image not found"));
    }
    Ok(Json(_image_to_dict(&img, session_name.as_deref())).into_response())
}

// ===========================================================================
// PATCH /api/gallery/{image_id}
// ===========================================================================

/// `patch_gallery_image(request, image_id, req: GalleryPatch)` — update tags /
/// favorite / album_id. Wrapped in `except Exception → 500 str(e)` (HTTPException
/// re-raised).
async fn patch_gallery_image(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(image_id): Path<String>,
    Json(req): Json<GalleryPatch>,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);
    let conn = session_local()?;

    // Load the full row (we need ai_tags for the cleaning + return _image_to_dict).
    let img: Option<GalleryImage> = conn
        .query_row(
            &format!("SELECT {IMG_COLS} FROM gallery_images WHERE id = ?1"),
            rusqlite::params![image_id],
            read_image,
        )
        .optional()
        .map_err(db_500)?;
    let mut img = match img {
        Some(i) => i,
        None => return Err(HttpException::new(404, "Image not found")),
    };
    if !owns_image(&conn, &image_id, user.as_deref())? {
        return Err(HttpException::new(404, "Image not found"));
    }

    // tags: drop any user-tag that already lives in ai_tags (case-insensitive),
    // de-dup case-insensitively, preserving first-seen order.
    if let Some(raw_tags) = req.tags.as_deref() {
        let ai_set: std::collections::HashSet<String> = img
            .ai_tags
            .as_deref()
            .unwrap_or("")
            .split(',')
            .filter_map(|t| {
                let t = t.trim();
                if t.is_empty() {
                    None
                } else {
                    Some(t.to_lowercase())
                }
            })
            .collect();
        let mut cleaned: Vec<String> = Vec::new();
        let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
        for raw in raw_tags.split(',') {
            let t = raw.trim();
            let k = t.to_lowercase();
            if t.is_empty() || seen.contains(&k) || ai_set.contains(&k) {
                continue;
            }
            seen.insert(k);
            cleaned.push(t.to_string());
        }
        img.tags = Some(cleaned.join(", "));
    }
    if let Some(fav) = req.favorite {
        img.favorite = Some(fav);
    }
    if let Some(a) = req.album_id.as_deref() {
        // `img.album_id = req.album_id if req.album_id else None` — ""→None.
        img.album_id = if a.is_empty() { None } else { Some(a.to_string()) };
    }

    // Persist the three columns + bump updated_at (db.commit; db.refresh).
    let now = crate::pydatetime::utcnow_naive_iso();
    if let Err(e) = conn.execute(
        "UPDATE gallery_images SET tags = ?1, favorite = ?2, album_id = ?3, updated_at = ?4 WHERE id = ?5",
        rusqlite::params![
            img.tags,
            img.favorite.map(|b| if b { 1 } else { 0 }),
            img.album_id,
            now,
            image_id
        ],
    ) {
        // `except Exception as e: db.rollback(); raise HTTPException(500, str(e))`.
        return Err(HttpException::new(500, e.to_string()));
    }
    img.updated_at = Some(now);

    // `return _image_to_dict(img)` (no session_name).
    Ok(Json(_image_to_dict(&img, None)).into_response())
}

// ===========================================================================
// POST /api/gallery/download-zip
// ===========================================================================

/// `gallery_download_zip(request)` — bundle owned image ids into one `.zip`.
///
/// PORT_NOW via the `zip` crate (`zip::ZipWriter` over `Cursor<Vec<u8>>` with
/// `Deflated`), mirroring `zipfile.ZipFile(buf, "w", ZIP_DEFLATED)`.
async fn gallery_download_zip(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);
    // `if not user: raise HTTPException(401, "Not authenticated")`.
    let user = match user {
        Some(u) => u,
        None => return Err(HttpException::new(401, "Not authenticated")),
    };

    // `try: data = await request.json() except Exception: data = {}`.
    let data: Value = serde_json::from_slice(&raw_body).unwrap_or_else(|_| json!({}));
    // `ids = data.get("ids") or []`.
    let ids: Vec<String> = data
        .get("ids")
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    if ids.is_empty() {
        return Err(HttpException::new(400, "No images specified"));
    }

    let conn = session_local()?;
    // `imgs = db.query(GalleryImage).filter(id.in_(ids), owner == user).all()`.
    let placeholders: Vec<String> = (0..ids.len()).map(|i| format!("?{}", i + 2)).collect();
    let sql = format!(
        "SELECT id, filename, prompt FROM gallery_images \
         WHERE owner = ?1 AND id IN ({})",
        placeholders.join(", ")
    );
    let mut params: Vec<&dyn rusqlite::ToSql> = Vec::with_capacity(ids.len() + 1);
    params.push(&user);
    for i in &ids {
        params.push(i);
    }
    let mut stmt = conn.prepare(&sql).map_err(db_500)?;
    let mut rows = stmt.query(params.as_slice()).map_err(db_500)?;
    let mut imgs: Vec<(String, String, Option<String>)> = Vec::new();
    while let Some(r) = rows.next().map_err(db_500)? {
        imgs.push((
            r.get::<_, String>(0).map_err(db_500)?,
            r.get::<_, String>(1).map_err(db_500)?,
            r.get::<_, Option<String>>(2).map_err(db_500)?,
        ));
    }
    if imgs.is_empty() {
        return Err(HttpException::new(404, "No images found"));
    }

    // Build the zip.
    use std::io::Write;
    let mut cursor = std::io::Cursor::new(Vec::<u8>::new());
    let mut used: std::collections::HashSet<String> = std::collections::HashSet::new();
    {
        let mut zf = zip::ZipWriter::new(&mut cursor);
        let opts = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated);
        for (id, filename, prompt) in &imgs {
            let src = std::path::Path::new("data").join("generated_images").join(filename);
            if !src.exists() {
                continue;
            }
            let bytes = match std::fs::read(&src) {
                Ok(b) => b,
                Err(_) => continue,
            };
            // `ext = os.path.splitext(filename)[1] or ".png"`.
            let ext = splitext_ext(filename);
            let ext = if ext.is_empty() { ".png".to_string() } else { ext };
            // `base = (prompt or "").strip() or splitext(filename)[0]`.
            let stem = splitext_stem(filename);
            let mut base = prompt.as_deref().unwrap_or("").trim().to_string();
            if base.is_empty() {
                base = stem;
            }
            // `base = re.sub(r"[^\w\-. ]+", "", base)[:60].strip() or img.id`.
            base = sanitize_arcname(&base);
            base = base.chars().take(60).collect::<String>().trim().to_string();
            if base.is_empty() {
                base = id.clone();
            }
            // Disambiguate duplicate arcnames: name, name-1, name-2, ...
            let mut name = format!("{base}{ext}");
            let mut i = 1;
            while used.contains(&name) {
                name = format!("{base}-{i}{ext}");
                i += 1;
            }
            used.insert(name.clone());
            if zf.start_file(&name, opts).is_err() {
                continue;
            }
            let _ = zf.write_all(&bytes);
        }
        if zf.finish().is_err() {
            return Err(HttpException::new(500, "Internal Server Error"));
        }
    }
    // `if not used: raise HTTPException(404, "No image files found on disk")`.
    if used.is_empty() {
        return Err(HttpException::new(404, "No image files found on disk"));
    }

    let body = cursor.into_inner();
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/zip")
        .header(
            header::CONTENT_DISPOSITION,
            "attachment; filename=\"gallery-photos.zip\"",
        )
        .body(Body::from(body))
        .map_err(|e| HttpException::new(500, e.to_string()))
}

// ===========================================================================
// POST /api/gallery/clear-user-tags  /  clear-ai-tags  /  dedupe-tags
// ===========================================================================

/// `clear_gallery_user_tags(request)` — wipe `tags` on every owned active image
/// that has tags. Returns `{ok, cleared}`. `except → 500 str(e)`.
async fn clear_gallery_user_tags(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);
    clear_tag_column(user.as_deref(), "tags").map(|cleared| {
        Json(json!({ "ok": true, "cleared": cleared })).into_response()
    })
}

/// `clear_gallery_ai_tags(request, image_id?)` — wipe `ai_tags`; optionally scope
/// to one image.
async fn clear_gallery_ai_tags(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<std::collections::HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);
    let image_id = non_empty_str(q.get("image_id"));
    let conn = session_local()?;

    // q = active images; _owner_filter; if image_id: filter id == image_id.
    let owner = _owner_filter(user.as_deref());
    let (owner_where, owner_bind): (String, Option<String>) = match owner {
        // user is None -> OwnerFilter::All: SQL "1" (match everything), NOT "0".
        OwnerFilter::All => ("1".to_string(), None),
        OwnerFilter::Owner(u) => ("owner = ?1".to_string(), Some(u)),
    };
    let mut wheres = vec!["is_active = 1".to_string(), owner_where];
    let mut binds: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();
    if let Some(u) = &owner_bind {
        binds.push(Box::new(u.clone()));
    }
    if let Some(iid) = &image_id {
        binds.push(Box::new(iid.clone()));
        wheres.push(format!("id = ?{}", binds.len()));
    }
    let where_sql = wheres.join(" AND ");
    let bind_refs: Vec<&dyn rusqlite::ToSql> = binds.iter().map(|p| p.as_ref()).collect();

    // Count rows whose ai_tags is truthy (Python: `if img.ai_tags: cleared += 1`).
    let cleared: i64 = conn
        .query_row(
            &format!(
                "SELECT COUNT(*) FROM gallery_images \
                 WHERE {where_sql} AND ai_tags IS NOT NULL AND ai_tags != ''"
            ),
            bind_refs.as_slice(),
            |r| r.get(0),
        )
        .map_err(db_500)?;
    if let Err(e) = conn.execute(
        &format!("UPDATE gallery_images SET ai_tags = '' WHERE {where_sql} AND ai_tags IS NOT NULL AND ai_tags != ''"),
        bind_refs.as_slice(),
    ) {
        return Err(HttpException::new(500, e.to_string()));
    }
    Ok(Json(json!({ "ok": true, "cleared": cleared })).into_response())
}

/// `dedupe_gallery_tags(request)` — drop any `tags` entry that also appears in
/// `ai_tags` (case-insensitive), per owned active image. Returns
/// `{ok, rows_touched, tags_removed}`.
async fn dedupe_gallery_tags(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);
    let conn = session_local()?;

    let owner = _owner_filter(user.as_deref());
    let (owner_where, owner_bind): (&str, Option<String>) = match owner {
        // user is None -> _owner_filter returns OwnerFilter::All: SQL "1" (match
        // everything), NOT "0" — no-auth/single-user mode sees the FULL library.
        OwnerFilter::All => ("1", None),
        OwnerFilter::Owner(u) => ("owner = ?1", Some(u)),
    };
    let sql = format!(
        "SELECT id, tags, ai_tags FROM gallery_images WHERE is_active = 1 AND {owner_where}"
    );
    let mut stmt = conn.prepare(&sql).map_err(db_500)?;
    let map = |r: &rusqlite::Row<'_>| -> rusqlite::Result<(String, Option<String>, Option<String>)> {
        Ok((r.get(0)?, r.get(1)?, r.get(2)?))
    };
    let rows: Vec<(String, Option<String>, Option<String>)> = match owner_bind {
        Some(u) => stmt
            .query_map(rusqlite::params![u], map)
            .map_err(db_500)?
            .collect::<rusqlite::Result<Vec<_>>>()
            .map_err(db_500)?,
        None => stmt
            .query_map([], map)
            .map_err(db_500)?
            .collect::<rusqlite::Result<Vec<_>>>()
            .map_err(db_500)?,
    };
    drop(stmt);

    let mut rows_touched: i64 = 0;
    let mut tags_removed: i64 = 0;
    let mut updates: Vec<(String, String)> = Vec::new();
    for (id, tags, ai_tags) in rows {
        let ai_set: std::collections::HashSet<String> = ai_tags
            .as_deref()
            .unwrap_or("")
            .split(',')
            .filter_map(|t| {
                let t = t.trim();
                if t.is_empty() {
                    None
                } else {
                    Some(t.to_lowercase())
                }
            })
            .collect();
        if ai_set.is_empty() {
            continue;
        }
        let original: Vec<String> = tags
            .as_deref()
            .unwrap_or("")
            .split(',')
            .filter_map(|t| {
                let t = t.trim();
                if t.is_empty() {
                    None
                } else {
                    Some(t.to_string())
                }
            })
            .collect();
        let mut cleaned: Vec<String> = Vec::new();
        let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
        for t in &original {
            let k = t.to_lowercase();
            if ai_set.contains(&k) || seen.contains(&k) {
                continue;
            }
            seen.insert(k);
            cleaned.push(t.clone());
        }
        if cleaned.len() != original.len() {
            rows_touched += 1;
            tags_removed += (original.len() - cleaned.len()) as i64;
            updates.push((id, cleaned.join(", ")));
        }
    }
    for (id, new_tags) in updates {
        if let Err(e) = conn.execute(
            "UPDATE gallery_images SET tags = ?1 WHERE id = ?2",
            rusqlite::params![new_tags, id],
        ) {
            return Err(HttpException::new(500, e.to_string()));
        }
    }
    Ok(Json(json!({
        "ok": true,
        "rows_touched": rows_touched,
        "tags_removed": tags_removed,
    }))
    .into_response())
}

/// Shared body for `clear-user-tags` (column == "tags"). Counts rows whose column
/// is truthy, then blanks the column, returning the count. `except → 500 str(e)`.
fn clear_tag_column(user: Option<&str>, column: &str) -> Result<i64, HttpException> {
    let conn = session_local()?;
    let owner = _owner_filter(user);
    let (owner_where, bind): (&str, Option<String>) = match owner {
        // user is None -> _owner_filter returns OwnerFilter::All: SQL "1" (match
        // everything), NOT "0" — no-auth/single-user mode sees the FULL library.
        OwnerFilter::All => ("1", None),
        OwnerFilter::Owner(u) => ("owner = ?1", Some(u)),
    };
    let count_sql = format!(
        "SELECT COUNT(*) FROM gallery_images \
         WHERE is_active = 1 AND {owner_where} AND {column} IS NOT NULL AND {column} != ''"
    );
    let update_sql = format!(
        "UPDATE gallery_images SET {column} = '' \
         WHERE is_active = 1 AND {owner_where} AND {column} IS NOT NULL AND {column} != ''"
    );
    let cleared: i64 = match bind {
        Some(ref u) => conn
            .query_row(&count_sql, rusqlite::params![u], |r| r.get(0))
            .map_err(db_500)?,
        None => conn.query_row(&count_sql, [], |r| r.get(0)).map_err(db_500)?,
    };
    let exec = match bind {
        Some(ref u) => conn.execute(&update_sql, rusqlite::params![u]),
        None => conn.execute(&update_sql, []),
    };
    if let Err(e) = exec {
        return Err(HttpException::new(500, e.to_string()));
    }
    Ok(cleared)
}

// ===========================================================================
// DELETE /api/gallery/{image_id}
// ===========================================================================

/// `delete_gallery_image(request, image_id)` — soft-delete + remove file + strip
/// stale chat-history references (best-effort). PORT_NOW, including the
/// ChatMessage metadata cleanup via raw SQL + JSON surgery.
async fn delete_gallery_image(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(image_id): Path<String>,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);
    let conn = session_local()?;

    // `img = ...first(); if not img: 404; if not user or img.owner != user: 404`.
    let filename: Option<String> = conn
        .query_row(
            "SELECT filename FROM gallery_images WHERE id = ?1",
            rusqlite::params![image_id],
            |r| r.get(0),
        )
        .optional()
        .map_err(db_500)?;
    let img_filename = match filename {
        Some(f) => f,
        None => return Err(HttpException::new(404, "Image not found")),
    };
    if !owns_image(&conn, &image_id, user.as_deref())? {
        return Err(HttpException::new(404, "Image not found"));
    }

    // Remove the file from disk.
    let img_path = std::path::Path::new("data")
        .join("generated_images")
        .join(&img_filename);
    if img_path.exists() {
        let _ = std::fs::remove_file(&img_path);
    }

    // Soft-delete the record.
    if let Err(e) = conn.execute(
        "UPDATE gallery_images SET is_active = 0 WHERE id = ?1",
        rusqlite::params![image_id],
    ) {
        // `except Exception as e: db.rollback(); raise HTTPException(500, str(e))`.
        return Err(HttpException::new(500, e.to_string()));
    }

    // Best-effort chat-history cleanup (the whole block is `try/except → warning`).
    if let Err(e) = cleanup_chat_history(&conn, &image_id, &img_filename) {
        crate::pylog::warning(&format!(
            "chat-history cleanup after image delete failed: {e}"
        ));
    }

    Ok(Json(json!({ "status": "deleted", "id": image_id })).into_response())
}

/// The ChatMessage metadata cleanup after an image delete.
///
/// Matches `chat_messages` rows whose `meta_data` mentions the image_id OR the
/// filename, parses the JSON `tool_events`, drops the matching event, and either
/// rewrites `meta_data` (events remain) or deletes the message (no events left) +
/// the immediately-preceding pure user prompt. Returns `rusqlite::Error` so the
/// caller logs a best-effort warning.
fn cleanup_chat_history(
    conn: &Connection,
    image_id: &str,
    img_filename: &str,
) -> rusqlite::Result<()> {
    let like_id = format!("%{image_id}%");
    let like_fn = format!("%{img_filename}%");
    let mut stmt = conn.prepare(
        "SELECT id, session_id, role, timestamp, meta_data FROM chat_messages \
         WHERE meta_data IS NOT NULL AND (meta_data LIKE ?1 OR meta_data LIKE ?2)",
    )?;
    struct Msg {
        id: i64,
        session_id: Option<String>,
        timestamp: Option<String>,
        meta_data: Option<String>,
    }
    let msgs: Vec<Msg> = stmt
        .query_map(rusqlite::params![like_id, like_fn], |r| {
            Ok(Msg {
                id: r.get(0)?,
                session_id: r.get(1)?,
                timestamp: r.get(3)?,
                meta_data: r.get(4)?,
            })
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    drop(stmt);

    let any = !msgs.is_empty();
    let mut rows_to_delete: Vec<i64> = Vec::new();
    let mut rewrites: Vec<(i64, String)> = Vec::new();

    for m in &msgs {
        let raw = match &m.meta_data {
            Some(s) => s,
            None => continue,
        };
        let mut meta: Value = match serde_json::from_str(raw) {
            Ok(v) => v,
            Err(_) => continue,
        };
        let events = meta
            .get("tool_events")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        let mut new_events: Vec<Value> = Vec::new();
        let mut removed_any = false;
        for ev in events {
            if !ev.is_object() {
                new_events.push(ev);
                continue;
            }
            let ev_image_id = ev.get("image_id").and_then(|v| v.as_str());
            let ev_image_url = ev.get("image_url").and_then(|v| v.as_str());
            let is_match = ev_image_id == Some(image_id)
                || ev_image_url
                    .map(|u| !u.is_empty() && u.contains(img_filename))
                    .unwrap_or(false);
            if is_match {
                removed_any = true;
                continue;
            }
            new_events.push(ev);
        }
        if !removed_any {
            continue;
        }
        if new_events.is_empty() {
            rows_to_delete.push(m.id);
            // Find the immediately-preceding message in the session (timestamp <).
            if let (Some(sid), Some(ts)) = (m.session_id.as_deref(), m.timestamp.as_deref()) {
                let prev: Option<(i64, Option<String>, Option<String>)> = conn
                    .query_row(
                        "SELECT id, role, meta_data FROM chat_messages \
                         WHERE session_id = ?1 AND timestamp < ?2 \
                         ORDER BY timestamp DESC LIMIT 1",
                        rusqlite::params![sid, ts],
                        |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
                    )
                    .optional()?;
                if let Some((prev_id, role, prev_meta)) = prev {
                    if role.as_deref() == Some("user") {
                        let prev_events_empty = match prev_meta.as_deref() {
                            Some(pm) if !pm.is_empty() => serde_json::from_str::<Value>(pm)
                                .ok()
                                .and_then(|v| {
                                    v.get("tool_events")
                                        .and_then(|e| e.as_array())
                                        .map(|a| a.is_empty())
                                })
                                .unwrap_or(true),
                            _ => true,
                        };
                        if prev_events_empty {
                            rows_to_delete.push(prev_id);
                        }
                    }
                }
            }
        } else {
            meta["tool_events"] = Value::Array(new_events);
            rewrites.push((m.id, serde_json::to_string(&meta).unwrap_or(raw.clone())));
        }
    }

    for (id, new_meta) in rewrites {
        conn.execute(
            "UPDATE chat_messages SET meta_data = ?1 WHERE id = ?2",
            rusqlite::params![new_meta, id],
        )?;
    }
    for id in rows_to_delete {
        conn.execute("DELETE FROM chat_messages WHERE id = ?1", rusqlite::params![id])?;
    }
    // `if msgs: db.commit()` — autocommit here, nothing extra to do.
    let _ = any;
    Ok(())
}

// ===========================================================================
// Album path-param routes: PUT/DELETE/add/remove
// ===========================================================================

/// `update_album(request, album_id)` — partial album update (name / description /
/// cover_id, the latter validated against an owned image).
async fn update_album(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(album_id): Path<String>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);
    let data: Value = serde_json::from_slice(&raw_body).unwrap_or(Value::Null);
    let mut conn = session_local()?;

    // Python mutates `album.name` / `.description` / `.cover_id` on the in-memory
    // ORM object and calls `db.commit()` EXACTLY ONCE at the end — AFTER the
    // `_get_or_404_image(db, cover_id, user)` validation. So a cover_id failure
    // (image missing / not owned) raises a 404 BEFORE the single commit, rolling
    // back the name/description mutations too: nothing is persisted. Mirror that
    // atomicity with a single rusqlite transaction — every UPDATE runs on `tx`,
    // and we `tx.commit()` only after the cover_id validation. Any early `?`
    // return drops `tx` un-committed -> rollback, so a cover_id 404 persists
    // NOTHING (matching Python's single-commit-at-end semantics).
    let tx = conn.transaction().map_err(db_500)?;

    get_or_404_album(&tx, &album_id, user.as_deref())?;

    // `if data.get("name") is not None: album.name = data["name"]`.
    let new_name = data.get("name").filter(|v| !v.is_null());
    let new_desc = data.get("description").filter(|v| !v.is_null());
    let new_cover = data.get("cover_id").filter(|v| !v.is_null());

    if let Some(n) = new_name {
        let n = n.as_str().unwrap_or("").to_string();
        tx.execute(
            "UPDATE gallery_albums SET name = ?1 WHERE id = ?2",
            rusqlite::params![n, album_id],
        )
        .map_err(db_500)?;
    }
    if let Some(d) = new_desc {
        let d = d.as_str().unwrap_or("").to_string();
        tx.execute(
            "UPDATE gallery_albums SET description = ?1 WHERE id = ?2",
            rusqlite::params![d, album_id],
        )
        .map_err(db_500)?;
    }
    if let Some(c) = new_cover {
        // `cover_id = data["cover_id"] or None` (truthiness).
        let cover_id = match c.as_str() {
            Some(s) if !s.is_empty() => Some(s.to_string()),
            _ => None,
        };
        // `if cover_id: _get_or_404_image(db, cover_id, user)` — runs BEFORE the
        // single commit, so a 404 here drops `tx` (rollback) and the
        // name/description UPDATEs above are NOT persisted.
        if let Some(cid) = &cover_id {
            get_or_404_image(&tx, cid, user.as_deref())?;
        }
        tx.execute(
            "UPDATE gallery_albums SET cover_id = ?1 WHERE id = ?2",
            rusqlite::params![cover_id, album_id],
        )
        .map_err(db_500)?;
    }

    // `db.commit()` — the single commit at the end.
    tx.commit().map_err(db_500)?;
    Ok(Json(json!({ "ok": true })).into_response())
}

/// `delete_album(request, album_id)` — detach the album's images then delete it.
async fn delete_album(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(album_id): Path<String>,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);
    let conn = session_local()?;
    get_or_404_album(&conn, &album_id, user.as_deref())?;
    // `db.query(GalleryImage).filter(album_id == album_id).update({"album_id": None})`.
    conn.execute(
        "UPDATE gallery_images SET album_id = NULL WHERE album_id = ?1",
        rusqlite::params![album_id],
    )
    .map_err(db_500)?;
    conn.execute(
        "DELETE FROM gallery_albums WHERE id = ?1",
        rusqlite::params![album_id],
    )
    .map_err(db_500)?;
    Ok(Json(json!({ "ok": true })).into_response())
}

/// `add_to_album(request, album_id)` — move owned images into the album.
async fn add_to_album(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(album_id): Path<String>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);
    let data: Value = serde_json::from_slice(&raw_body).unwrap_or(Value::Null);
    // `ids = data.get("image_ids", [])`.
    let ids: Vec<String> = data
        .get("image_ids")
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect())
        .unwrap_or_default();
    let conn = session_local()?;
    get_or_404_album(&conn, &album_id, user.as_deref())?;
    set_album_for_ids(&conn, &ids, Some(&album_id), user.as_deref(), None)?;
    Ok(Json(json!({ "ok": true, "count": ids.len() })).into_response())
}

/// `remove_from_album(request, album_id)` — detach owned images currently in the
/// album (the extra `album_id == album_id` filter is the Python's safety scope).
async fn remove_from_album(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(album_id): Path<String>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);
    let data: Value = serde_json::from_slice(&raw_body).unwrap_or(Value::Null);
    let ids: Vec<String> = data
        .get("image_ids")
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect())
        .unwrap_or_default();
    let conn = session_local()?;
    get_or_404_album(&conn, &album_id, user.as_deref())?;
    // `filter(id.in_(ids), album_id == album_id).update({"album_id": None})`.
    set_album_for_ids(&conn, &ids, None, user.as_deref(), Some(&album_id))?;
    Ok(Json(json!({ "ok": true })).into_response())
}

/// Bulk `UPDATE gallery_images SET album_id = <new>` over `id IN (...)`, with an
/// optional `owner = user` scope (when a user resolves) and an optional
/// `album_id = <current>` scope (the remove-from-album safety). An empty id list
/// is a no-op (matching `IN ()` matching nothing).
fn set_album_for_ids(
    conn: &Connection,
    ids: &[String],
    new_album: Option<&str>,
    user: Option<&str>,
    current_album: Option<&str>,
) -> Result<(), HttpException> {
    if ids.is_empty() {
        return Ok(());
    }
    let mut binds: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();
    // SET value (positional ?1) — `Some(album)` or SQL NULL.
    binds.push(Box::new(new_album.map(str::to_string)));
    let mut wheres: Vec<String> = Vec::new();
    // id IN (...)
    let mut placeholders = Vec::new();
    for id in ids {
        binds.push(Box::new(id.clone()));
        placeholders.push(format!("?{}", binds.len()));
    }
    wheres.push(format!("id IN ({})", placeholders.join(", ")));
    if let Some(ca) = current_album {
        binds.push(Box::new(ca.to_string()));
        wheres.push(format!("album_id = ?{}", binds.len()));
    }
    if let Some(u) = user {
        binds.push(Box::new(u.to_string()));
        wheres.push(format!("owner = ?{}", binds.len()));
    }
    let sql = format!(
        "UPDATE gallery_images SET album_id = ?1 WHERE {}",
        wheres.join(" AND ")
    );
    let bind_refs: Vec<&dyn rusqlite::ToSql> = binds.iter().map(|p| p.as_ref()).collect();
    conn.execute(&sql, bind_refs.as_slice()).map_err(db_500)?;
    Ok(())
}

// ===========================================================================
// POST /api/gallery/{image_id}/favorite
// ===========================================================================

/// `toggle_favorite(request, image_id)` — flip the favorite flag.
async fn toggle_favorite(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(image_id): Path<String>,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);
    let conn = session_local()?;
    // `img = _get_or_404_image(db, image_id, user)`.
    let img = get_or_404_image(&conn, &image_id, user.as_deref())?;
    // `img.favorite = not img.favorite`. SQLAlchemy default is False; NULL→treated
    // as falsy by the Python `not`, so NULL flips to True.
    let new_fav = !img.favorite.unwrap_or(false);
    conn.execute(
        "UPDATE gallery_images SET favorite = ?1 WHERE id = ?2",
        rusqlite::params![if new_fav { 1 } else { 0 }, image_id],
    )
    .map_err(db_500)?;
    Ok(Json(json!({ "ok": true, "favorite": new_fav })).into_response())
}

// ===========================================================================
// POST /api/gallery/{image_id}/ai-tag  — PORT_NOW disabled-branch; DEFER resolve
// ===========================================================================

/// `ai_tag_image(request, image_id)` — PORT the portable branches; honest-stub
/// fully ported: owner-scoped 404, the file-existence check, the base64/mime
/// computation, `_load_vl_settings()` + the `vision_enabled` gate, the
/// `_resolve_vl_model(configured)` resolution, the vision call, the tag parse,
/// and the `img.ai_tags` persist.
///
/// `_load_vl_settings()` is `load_settings()` wrapped in `try/except → {}`
/// (src/document_processor.py:155); the Rust `crate::src::settings::load_settings()`
/// never raises and returns the merged-over-defaults map, so the
/// `.get("vision_enabled", True)` lookup is a faithful port. When vision is
/// disabled this returns the exact Python string
/// `{"error": "Vision is disabled — enable it in Settings → Vision"}`.
///
/// The vision call routes through `document_processor::vl_chat` → `llm_call_async`,
/// which converts the OpenAI-shaped `image_url` content block into the right
/// provider request internally (`_detect_provider`). ACCEPTED BYTE-DRIFT vs the
/// Python gallery handler (gallery_routes.py:1701-1727): Python hand-rolls its
/// Anthropic payload as `{model, max_tokens:200, messages:[image-block,
/// text-block]}` with NO `temperature` and image-FIRST. Routing through the shared
/// `vl_chat`/`llm_call_async`/`_build_anthropic_payload` path instead yields
/// `temperature:0.3` present + text-FIRST block order. Both are valid Anthropic
/// requests and the OBSERVABLE result (tags parsed from the reply text) is
/// unchanged; we accept this to keep ONE shared vision path (see PARITY_GAPS).
/// The OpenAI byte-shape is unchanged.
async fn ai_tag_image(
    State(_s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(image_id): Path<String>,
) -> Result<Response, HttpException> {
    let user: Option<String> = current_user(user);
    let conn = session_local()?;

    // `img = _get_or_404_image(db, image_id, user)`.
    let img = get_or_404_image(&conn, &image_id, user.as_deref())?;

    // `img_path = Path("data/generated_images")/img.filename; if not exists: 404`.
    let filename = img.filename.clone().unwrap_or_default();
    let img_path = std::path::Path::new(IMG_DIR).join(&filename);
    if !img_path.exists() {
        return Err(HttpException::new(404, "Image file not found"));
    }

    // `img_bytes = img_path.read_bytes(); b64 = base64encode(...).decode()`.
    // mime is the same dict mapping keyed on the lowercased extension. The read
    // is inside Python's broad `try` (caught as `{"error": str(e)}`, a 200), not a
    // raise — reproduce that soft-fail rather than a 500.
    let img_bytes = match std::fs::read(&img_path) {
        Ok(b) => b,
        Err(e) => return Ok(Json(json!({ "error": e.to_string() })).into_response()),
    };
    let b64 = base64_std(&img_bytes);
    let ext = filename.rsplit('.').next().unwrap_or("").to_lowercase();
    let mime: &str = match ext.as_str() {
        "jpg" | "jpeg" => "image/jpeg",
        "png" => "image/png",
        "webp" => "image/webp",
        "gif" => "image/gif",
        _ => "image/jpeg",
    };

    // `vl_settings = _load_vl_settings()` — `load_settings()` (try/except → {}).
    // `if not vl_settings.get("vision_enabled", True): return {"error": ...}`.
    let vl_settings = crate::src::settings::load_settings();
    let vision_enabled = vl_settings
        .get("vision_enabled")
        .and_then(|v| v.as_bool())
        .unwrap_or(true);
    if !vision_enabled {
        return Ok(Json(json!({
            "error": "Vision is disabled — enable it in Settings → Vision"
        }))
        .into_response());
    }

    // `configured = vl_settings.get("vision_model", "")`.
    let configured = vl_settings
        .get("vision_model")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    // `chat_url, model_name, headers = _resolve_vl_model(configured)` — Python
    // catches `ValueError` (unconfigured/unknown model) and returns the
    // "No vision model configured" error. The Rust `resolve_vl_model` can also
    // surface other `Err` variants (endpoint probe failures); match the broad
    // Python intent — any resolve failure returns that same error key (a 200 with
    // an error key, never a raise).
    let (url, model, headers) =
        match crate::src::document_processor::resolve_vl_model(&configured).await {
            Ok(t) => t,
            Err(_) => {
                return Ok(Json(json!({
                    "error": "No vision model configured — set one in Settings → Vision"
                }))
                .into_response());
            }
        };
    // `if not chat_url: return {"error": "No vision-capable endpoint configured"}`.
    if url.is_empty() {
        return Ok(Json(json!({
            "error": "No vision-capable endpoint configured"
        }))
        .into_response());
    }

    // `tag_prompt` — verbatim from gallery_routes.py:1693-1699.
    let tag_prompt = "Analyze this photo. Return ONLY a comma-separated list of tags. \
Include: objects, people (describe by appearance — age range, gender), \
scene/setting, activities, mood/atmosphere, colors, location type, \
time of day, weather if visible, any text/signs visible. \
Be specific but concise. 10-25 tags. No explanation, just tags.";

    // The shared vision call. `mime` here is the FULL media type ("image/jpeg"),
    // so the data URI is `data:{mime};base64,{b64}` (py:1722). `vl_chat` builds the
    // OpenAI-shaped message; `llm_call_async` routes the provider internally.
    // temp=0.3, max_tokens=200, timeout=60 (py httpx `timeout=60`).
    let data_uri = format!("data:{mime};base64,{b64}");
    let content = match crate::src::document_processor::vl_chat(
        &url, &model, headers, &data_uri, tag_prompt, None, 0.3, 200, 60,
    )
    .await
    {
        Ok(c) => c,
        // Python broad-`except Exception as e: return {"error": str(e)}`.
        Err(e) => {
            crate::pylog::error(&format!("AI tagging failed: {e}"));
            return Ok(Json(json!({ "error": e.to_string() })).into_response());
        }
    };

    // Clean up tags: `[t.strip().lower() for t in content.split(",") if t.strip()]`.
    let tags: Vec<String> = content
        .split(',')
        .filter_map(|t| {
            let t = t.trim();
            if t.is_empty() {
                None
            } else {
                Some(t.to_lowercase())
            }
        })
        .collect();
    // `tag_str = ", ".join(tags[:30])`.
    let tag_str = tags
        .iter()
        .take(30)
        .cloned()
        .collect::<Vec<_>>()
        .join(", ");

    // `img.ai_tags = tag_str; db.commit()`. Owner scope was enforced by
    // `get_or_404_image` above; write by id.
    conn.execute(
        "UPDATE gallery_images SET ai_tags = ?1 WHERE id = ?2",
        rusqlite::params![tag_str, image_id],
    )
    .map_err(db_500)?;

    Ok(Json(json!({ "ok": true, "ai_tags": tag_str })).into_response())
}

// ===========================================================================
// POST /api/image/inpaint  — reqwest proxy (self-hosted + OpenAI, both real)
// ===========================================================================

/// `inpaint_proxy(request)` — forward an inpaint request to a diffusion server.
///
/// The self-hosted diffusion path is a reqwest proxy of the JSON body to
/// `{base}/v1/images/inpaint`. The OpenAI branch (`"api.openai.com" in base`) is a
/// REAL port of `gallery_routes.py:961-1063`: it converts the SD white-on-black
/// mask to OpenAI's alpha convention (`image_edit::sd_mask_to_openai_alpha`),
/// POSTs a `/v1/images/edits` multipart request, then composites the model output
/// back onto the source through the user's mask (`image_edit::composite_with_mask`)
/// so only the masked region changes — falling back to the raw OpenAI output if
/// compositing fails, exactly like the Python.
async fn inpaint_proxy(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    connect_info: Option<ConnectInfo<SocketAddr>>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    // `require_privilege(request, "can_generate_images")` (gallery_routes.py:922) —
    // runs BEFORE the body is parsed.
    require_can_generate_images(user, &s, connect_info)?;
    let mut body: Value = serde_json::from_slice(&raw_body).unwrap_or(Value::Null);

    // `base = (body.pop("_endpoint", "") or "").rstrip("/")`.
    let base_in = pop_str(&mut body, "_endpoint");
    let mut base = base_in.trim_end_matches('/').to_string();
    // SSRF hardening (gallery_routes.py:925-934): validate a client-supplied
    // endpoint before any outbound request. `if base:` — only the explicit
    // `_endpoint` is checked (the DB-fallback base below is trusted/stored).
    // `block_private = os.getenv("IMAGE_BLOCK_PRIVATE_IPS","false").lower()=="true"`.
    if !base.is_empty() {
        let block_private =
            crate::pyos::getenv("IMAGE_BLOCK_PRIVATE_IPS", "false").to_lowercase() == "true";
        let (ok, reason) = crate::src::url_safety::check_outbound_url(&base, block_private);
        if !ok {
            return Err(HttpException::new(
                400,
                format!("Rejected endpoint URL: {reason}"),
            ));
        }
    }
    let chosen_model = pop_str(&mut body, "_model");
    let chosen_model = chosen_model.trim().to_string();
    // `api_key` is resolved exactly as the Python does (the DB-lookup side-effect
    // is preserved for fidelity). It is consumed on the OpenAI branch (the
    // `if not api_key` 400 below) and ignored on the self-hosted diffusion path,
    // which never sends auth (matching Python).
    let api_key: Option<String> = if base.is_empty() {
        // DB lookup: first enabled image endpoint.
        let ep = first_image_endpoint()?;
        match ep {
            None => {
                return Err(HttpException::new(
                    400,
                    "No image generation endpoint configured. Serve a diffusion model via Cookbook first.",
                ))
            }
            Some(e) => {
                base = e.base_url.trim_end_matches('/').to_string();
                e.api_key
            }
        }
    } else {
        // Pull api_key from the matching DB row (normalized compare).
        api_key_for_normalized_base(&base)
    };

    if !base.ends_with("/v1") {
        base.push_str("/v1");
    }
    let is_openai = base.contains("api.openai.com");

    if is_openai {
        // `if not api_key: raise HTTPException(400, "OpenAI endpoint has no
        // api_key stored — edit it in Endpoints settings.")` — runs BEFORE the
        // PIL import, so a key-less OpenAI endpoint 400s rather than 500ing.
        if api_key.as_deref().unwrap_or("").is_empty() {
            return Err(HttpException::new(
                400,
                "OpenAI endpoint has no api_key stored — edit it in Endpoints settings.",
            ));
        }
        // ---- prepare source + OpenAI alpha mask (py:967-985) ----
        // `img_bytes = b64decode(body["image"]); mask_bytes = b64decode(body["mask"])`.
        // KeyError / decode / image-open failure → HTTPException(400, "Failed to
        // prepare OpenAI request: {e}"), matching py:988-989.
        let prep: Result<(image::RgbaImage, image::GrayImage, Vec<u8>, Vec<u8>), String> = (|| {
            let img_b64 = body
                .get("image")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "'image'".to_string())?;
            let mask_b64 = body
                .get("mask")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "'mask'".to_string())?;
            let source = image_edit::decode_image_b64(img_b64)?;
            let mask = image_edit::decode_image_b64(mask_b64)?;
            // `.convert("RGBA")` / `.convert("L")`.
            let source_png = source.to_rgba8();
            let mask_l = mask.to_luma8();
            let size = (source_png.width(), source_png.height());
            // `oa_mask = Image.new("RGBA", source.size, (255,255,255,255));
            //  oa_mask.putalpha(255 - mask)` (py:976-978).
            let oa_mask = image_edit::sd_mask_to_openai_alpha(&mask_l, size);
            // PNG-encode source + oa_mask (py:980-985).
            let src_bytes =
                image_edit::encode_png_b64(&image::DynamicImage::ImageRgba8(source_png.clone()))?;
            let mask_bytes =
                image_edit::encode_png_b64(&image::DynamicImage::ImageRgba8(oa_mask))?;
            use base64::Engine;
            let src_png = base64::engine::general_purpose::STANDARD
                .decode(src_bytes.as_bytes())
                .map_err(|e| e.to_string())?;
            let mask_png = base64::engine::general_purpose::STANDARD
                .decode(mask_bytes.as_bytes())
                .map_err(|e| e.to_string())?;
            Ok((source_png, mask_l, src_png, mask_png))
        })();
        let (source_png, mask_l, src_png, mask_png) = match prep {
            Ok(t) => t,
            Err(e) => {
                return Err(HttpException::new(
                    400,
                    format!("Failed to prepare OpenAI request: {e}"),
                ))
            }
        };

        // `width = int(body.get("width") or 1024); height = int(... or 1024)` then
        // pick the closest gpt-image-1 size (py:991-1000).
        let width = coerce_f64(body.get("width"), 0.0);
        let width = if width <= 0.0 { 1024.0 } else { width };
        let height = coerce_f64(body.get("height"), 0.0);
        let height = if height <= 0.0 { 1024.0 } else { height };
        let size = if width > height * 1.15 {
            "1536x1024"
        } else if height > width * 1.15 {
            "1024x1536"
        } else {
            "1024x1024"
        };

        // `oa_model = chosen_model or "gpt-image-1"`; dall-e-3 has no edits → 400.
        let oa_model = if chosen_model.is_empty() {
            "gpt-image-1".to_string()
        } else {
            chosen_model.clone()
        };
        if oa_model.contains("dall-e-3") {
            return Err(HttpException::new(
                400,
                "dall-e-3 doesn't support image edits — pick gpt-image-1 or dall-e-2",
            ));
        }

        // `prompt = body.get("prompt", "")`.
        let prompt = body
            .get("prompt")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let key = api_key.clone().unwrap_or_default();

        // `async with httpx.AsyncClient(timeout=120): r = await client.post(
        //  f"{base}/images/edits", headers, data, files)` (py:1018-1022).
        let client = match reqwest::Client::builder()
            .timeout(std::time::Duration::from_secs(120))
            .build()
        {
            Ok(c) => c,
            Err(e) => return Err(HttpException::new(502, format!("Inpaint error: {e}"))),
        };
        let form = reqwest::multipart::Form::new()
            .part(
                "image",
                reqwest::multipart::Part::bytes(src_png)
                    .file_name("source.png")
                    .mime_str("image/png")
                    .map_err(|e| HttpException::new(502, format!("Inpaint error: {e}")))?,
            )
            .part(
                "mask",
                reqwest::multipart::Part::bytes(mask_png)
                    .file_name("mask.png")
                    .mime_str("image/png")
                    .map_err(|e| HttpException::new(502, format!("Inpaint error: {e}")))?,
            )
            .text("model", oa_model)
            .text("prompt", prompt)
            .text("size", size.to_string())
            .text("n", "1");

        let send = client
            .post(format!("{base}/images/edits"))
            .header("Authorization", format!("Bearer {key}"))
            .multipart(form)
            .send()
            .await;
        let resp = match send {
            Ok(r) => r,
            Err(e) if e.is_timeout() => {
                return Err(HttpException::new(504, "OpenAI inpaint timed out (120s)"))
            }
            Err(e) => return Err(HttpException::new(502, format!("Inpaint error: {}", reqwest_err(&e)))),
        };
        // `if r.status_code != 200: raise HTTPException(r.status_code, f"OpenAI
        //  edit failed: {r.text[:300]}")` (py:1021-1022).
        let status = resp.status().as_u16();
        if status != 200 {
            let text = resp.text().await.unwrap_or_default();
            let snip: String = text.chars().take(300).collect();
            return Err(HttpException::new(status, format!("OpenAI edit failed: {snip}")));
        }
        // `result = r.json()` → pull b64_json, else fetch url (py:1023-1036).
        let result: Value = resp.json().await.unwrap_or(Value::Null);
        let mut raw_b64: Option<String> = None;
        if let Some(item) = result
            .get("data")
            .and_then(|d| d.as_array())
            .and_then(|a| a.first())
        {
            if let Some(b) = item.get("b64_json").and_then(|v| v.as_str()) {
                raw_b64 = Some(b.to_string());
            } else if let Some(url) = item.get("url").and_then(|v| v.as_str()) {
                // `async with httpx.AsyncClient(timeout=60) as c2: img_r = await
                //  c2.get(url); if 200: raw_b64 = b64encode(img_r.content)`.
                if let Ok(c2) = reqwest::Client::builder()
                    .timeout(std::time::Duration::from_secs(60))
                    .build()
                {
                    if let Ok(ir) = c2.get(url).send().await {
                        if ir.status().as_u16() == 200 {
                            if let Ok(bytes) = ir.bytes().await {
                                raw_b64 = Some(base64_std(&bytes));
                            }
                        }
                    }
                }
            }
        }
        // `if not raw_b64: raise HTTPException(502, "OpenAI returned no image")`.
        let raw_b64 = match raw_b64 {
            Some(b) => b,
            None => return Err(HttpException::new(502, "OpenAI returned no image")),
        };

        // `generated = ...; blended = Image.composite(generated, source_png,
        //  mask_png); return {"image": b64(blended)}` — on any failure fall back to
        //  the raw OpenAI output (py:1044-1061).
        let composed: Result<String, String> = (|| {
            let generated = image_edit::decode_image_b64(&raw_b64)?;
            let blended = image_edit::composite_with_mask(&generated, &source_png, &mask_l);
            image_edit::encode_png_b64(&image::DynamicImage::ImageRgba8(blended))
        })();
        return match composed {
            Ok(b64) => Ok(Json(json!({ "image": b64 })).into_response()),
            Err(comp_err) => {
                crate::pylog::warning(&format!(
                    "Inpaint compose failed, returning raw: {comp_err}"
                ));
                Ok(Json(json!({ "image": raw_b64 })).into_response())
            }
        };
    }

    // Self-hosted diffusion server path (PORT_NOW).
    // `if chosen_model: body["model"] = chosen_model`.
    if !chosen_model.is_empty() {
        if let Value::Object(map) = &mut body {
            map.insert("model".to_string(), json!(chosen_model));
        }
    }
    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(120))
        .build()
    {
        Ok(c) => c,
        Err(e) => return Err(HttpException::new(502, format!("Inpaint error: {e}"))),
    };
    let send = client
        .post(format!("{base}/images/inpaint"))
        .json(&body)
        .send()
        .await;
    match send {
        Ok(resp) => {
            let status = resp.status().as_u16();
            if status != 200 {
                let text = resp.text().await.unwrap_or_default();
                let snip: String = text.chars().take(200).collect();
                return Err(HttpException::new(status, format!("Inpaint failed: {snip}")));
            }
            let data: Value = resp.json().await.unwrap_or(Value::Null);
            Ok(Json(data).into_response())
        }
        Err(e) if e.is_timeout() => {
            Err(HttpException::new(504, "Inpaint request timed out (120s)"))
        }
        Err(e) => Err(HttpException::new(502, format!("Inpaint error: {}", reqwest_err(&e)))),
    }
}

// ===========================================================================
// POST /api/image/harmonize  — img2img proxy (PORT_NOW)
// ===========================================================================

/// `harmonize_image(request)` — img2img harmonize. Tries `/images/harmonize`,
/// `/images/img2img`, `/images/variations`, then the A1111 `/sdapi/v1/img2img`,
/// returning the first 200 with an image. PORT_NOW (no PIL). The OpenAI base is
/// refused with the Python's 400 message.
async fn harmonize_image(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    connect_info: Option<ConnectInfo<SocketAddr>>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    // `require_privilege(request, "can_generate_images")` (gallery_routes.py:1120) —
    // runs BEFORE the body is parsed.
    require_can_generate_images(user, &s, connect_info)?;
    let body: Value = serde_json::from_slice(&raw_body).unwrap_or(Value::Null);

    // `image_b64 = body.get("image"); if not image_b64: raise 400`.
    let image_b64 = body.get("image").and_then(|v| v.as_str()).unwrap_or("");
    if image_b64.is_empty() {
        return Err(HttpException::new(400, "No image provided"));
    }

    let endpoint = body
        .get("_endpoint")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim_end_matches('/')
        .to_string();
    // SSRF hardening (gallery_routes.py:1128-1139): a client-supplied endpoint is
    // fetched server-side below, so validate it first. `if endpoint:` — only the
    // explicit `_endpoint` is checked. Local-first allows loopback/LAN by default;
    // `IMAGE_BLOCK_PRIVATE_IPS=true` locks that down too.
    if !endpoint.is_empty() {
        let block_private =
            crate::pyos::getenv("IMAGE_BLOCK_PRIVATE_IPS", "false").to_lowercase() == "true";
        let (ok, reason) = crate::src::url_safety::check_outbound_url(&endpoint, block_private);
        if !ok {
            return Err(HttpException::new(
                400,
                format!("Rejected endpoint URL: {reason}"),
            ));
        }
    }
    let model = body
        .get("_model")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_string();

    let mut base = endpoint.clone();
    let api_key: Option<String> = if base.is_empty() {
        let ep = first_image_endpoint()?;
        match ep {
            None => {
                return Err(HttpException::new(
                    400,
                    "No image generation endpoint configured.",
                ))
            }
            Some(e) => {
                base = e.base_url.trim_end_matches('/').to_string();
                e.api_key
            }
        }
    } else {
        // `ep.base_url.rstrip("/").rstrip("/v1") == base.rstrip("/v1")`.
        api_key_for_rstrip_v1(&base)
    };
    if !base.ends_with("/v1") {
        base.push_str("/v1");
    }

    // prompt + strength + color_match + seam_fix coercion.
    let prompt = match body.get("prompt").and_then(|v| v.as_str()) {
        Some(p) if !p.is_empty() => p.to_string(),
        _ => "natural lighting, harmonious color, seamless blend".to_string(),
    };
    let strength = coerce_f64(body.get("strength"), 0.45).clamp(0.05, 0.95);
    let color_match = coerce_f64(body.get("color_match"), strength).clamp(0.0, 1.0);
    let seam_fix = coerce_f64(body.get("seam_fix"), 0.0).clamp(0.0, 1.0);
    let body_mask = body
        .get("body_mask")
        .or_else(|| body.get("mask"))
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty());
    let seam_mask = body
        .get("seam_mask")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty());

    if base.contains("api.openai.com") {
        return Err(HttpException::new(
            400,
            "Harmonize needs a diffusion server that supports img2img \
             (SD WebUI / Forge / Comfy). OpenAI's API doesn't expose \
             one. Cookbook → Models can serve an SD-compatible model \
             locally in a few clicks.",
        ));
    }

    // harmonize payload.
    let mut harmonize_payload = json!({
        "image": image_b64,
        "prompt": prompt,
        "color_match": color_match,
        "seam_fix": seam_fix,
        "strength": color_match,
    });
    if let Some(bm) = body_mask {
        harmonize_payload["body_mask"] = json!(bm);
        harmonize_payload["mask"] = json!(bm);
    }
    if let Some(sm) = seam_mask {
        harmonize_payload["seam_mask"] = json!(sm);
    }

    let model_obj = |m: &str| -> Value {
        if m.is_empty() {
            json!({})
        } else {
            json!({ "model": m })
        }
    };
    let mut img2img = json!({ "image": image_b64, "prompt": prompt, "strength": strength });
    merge_obj(&mut img2img, model_obj(&model));
    let mut variations = json!({ "image": image_b64, "prompt": prompt, "strength": strength });
    merge_obj(&mut variations, model_obj(&model));
    let mut a1111 = json!({
        "init_images": [format!("data:image/png;base64,{image_b64}")],
        "prompt": prompt,
        "denoising_strength": strength,
        "steps": 30,
    });
    if !model.is_empty() {
        a1111["override_settings"] = json!({ "sd_model_checkpoint": model });
    }

    let candidates: Vec<(&str, Value)> = vec![
        ("/images/harmonize", harmonize_payload),
        ("/images/img2img", img2img),
        ("/images/variations", variations),
        ("/sdapi/v1/img2img", a1111),
    ];

    // `base_root = base[:-3] if base.endswith("/v1") else base`.
    let base_root = base.strip_suffix("/v1").unwrap_or(&base).to_string();

    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(240))
        .build()
    {
        Ok(c) => c,
        Err(e) => return Err(HttpException::new(502, format!("Can't reach diffusion server at {base}: {e}"))),
    };

    let mut last_err: Option<String> = None;
    for (path, payload) in candidates {
        let target = if path.starts_with("/sdapi") {
            format!("{base_root}{path}")
        } else {
            format!("{base}{path}")
        };
        let mut req = client.post(&target);
        if let Some(k) = api_key.as_deref().filter(|k| !k.is_empty()) {
            req = req.header("Authorization", format!("Bearer {k}"));
        }
        let resp = match req.json(&payload).send().await {
            Ok(r) => r,
            Err(e) if e.is_connect() => {
                return Err(HttpException::new(
                    502,
                    format!("Can't reach diffusion server at {base}: {}", reqwest_err(&e)),
                ))
            }
            Err(e) if e.is_timeout() => {
                return Err(HttpException::new(
                    504,
                    "Harmonize timed out (240s) — restart the diffusion server or lower Color match / disable Seam fix",
                ))
            }
            Err(e) => {
                // Other transport errors aren't separately handled in Python's loop;
                // they'd bubble out of the `async with` as a 500. We surface 502 for
                // a generic transport failure (closest faithful behavior).
                return Err(HttpException::new(502, format!("Can't reach diffusion server at {base}: {}", reqwest_err(&e))));
            }
        };
        let status = resp.status().as_u16();
        if status == 404 {
            last_err = Some(format!("{path}: 404"));
            continue;
        }
        if status != 200 {
            let text = resp.text().await.unwrap_or_default();
            let snip: String = text.chars().take(120).collect();
            last_err = Some(format!("{path}: {status} {snip}"));
            continue;
        }
        let data: Value = resp.json().await.unwrap_or(Value::Null);
        if let Value::Object(_) = &data {
            // explicit error field (no image) → 502.
            let has_image = data.get("image").map(json_truthy).unwrap_or(false);
            if let Some(err) = data.get("error").filter(|v| json_truthy(v)) {
                if !has_image {
                    let err_s = err.as_str().map(str::to_string).unwrap_or_else(|| err.to_string());
                    return Err(HttpException::new(
                        502,
                        format!("Diffusion server error at {path}: {err_s}"),
                    ));
                }
            }
            if has_image {
                return Ok(Json(json!({ "image": data["image"] })).into_response());
            }
            // images: [str]
            if let Some(imgs) = data.get("images").and_then(|v| v.as_array()) {
                if let Some(Value::String(img0)) = imgs.first() {
                    let img0 = if let Some(rest) = img0.strip_prefix("data:") {
                        // split(",",1)[1]
                        rest.split_once(',')
                            .map(|(_, after)| after)
                            .unwrap_or(rest)
                            .to_string()
                    } else {
                        img0.clone()
                    };
                    return Ok(Json(json!({ "image": img0 })).into_response());
                }
            }
            // OpenAI-style data:[{b64_json|url}]
            if let Some(item) = data.get("data").and_then(|d| d.get(0)) {
                if let Some(b64) = item.get("b64_json").and_then(|v| v.as_str()) {
                    return Ok(Json(json!({ "image": b64 })).into_response());
                }
                if let Some(url) = item.get("url").and_then(|v| v.as_str()) {
                    if let Ok(c2) = reqwest::Client::builder()
                        .timeout(std::time::Duration::from_secs(60))
                        .build()
                    {
                        if let Ok(ir) = c2.get(url).send().await {
                            if ir.status().as_u16() == 200 {
                                if let Ok(bytes) = ir.bytes().await {
                                    return Ok(Json(json!({ "image": base64_std(&bytes) })).into_response());
                                }
                            }
                        }
                    }
                }
            }
        }
        last_err = Some(format!("{path}: server returned no image"));
    }

    Err(HttpException::new(
        502,
        format!(
            "None of the img2img routes worked on {base}. Last response: {}. \
             Your diffusion server needs to expose one of /v1/images/harmonize, \
             /v1/images/img2img, /v1/images/variations, or /sdapi/v1/img2img.",
            last_err.unwrap_or_else(|| "unknown".to_string())
        ),
    ))
}

// ===========================================================================
// PIL / ML editor ops — REAL ports (image/imageops CPU + ort/ONNX ML)
// ===========================================================================

/// `sharpen_image(request)` — REAL port of `gallery_routes.py:1274-1293`.
///
/// `img = b64decode(body["image"]).convert("RGB"); sharpened =
/// img.filter(ImageFilter.UnsharpMask(radius=2, percent=int(amount*200),
/// threshold=3))` where `amount = body.get("amount", 50) / 100.0`. Returns
/// `{"image": base64-png}`.
///
/// FAITHFULNESS NOTE: Python's `sharpen_image` has NO input guard — a missing or
/// invalid `image` raises inside `base64.b64decode` / `Image.open`, surfacing as a
/// Starlette 500. We mirror that: a decode failure here returns a 500 (NOT a 400),
/// since the other "no image" 400 guards exist only on handlers that have them in
/// Python (denoise / upscale / enhance-face).
async fn sharpen_image(
    State(_s): State<AppState>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let body: Value = serde_json::from_slice(&raw_body).unwrap_or(Value::Null);
    // `amount = body.get("amount", 50) / 100.0` — Python coerces the JSON number.
    let amount = coerce_f64(body.get("amount"), 50.0) / 100.0;
    // `img_bytes = base64.b64decode(image_b64)` — no guard; failure → 500.
    let image_b64 = body.get("image").and_then(|v| v.as_str()).unwrap_or("");
    let decoded = image_edit::decode_image_b64(image_b64)
        .map_err(|e| HttpException::new(500, format!("Internal Server Error: {e}")))?;
    // `.convert("RGB")` then UnsharpMask. `image_edit::unsharp_mask` takes RGBA;
    // to_rgba8 on an RGB-decoded image is the faithful (opaque) lift, and the
    // op only touches RGB channels.
    let rgba = decoded.to_rgba8();
    // `ImageFilter.UnsharpMask(radius=2, percent=int(amount*200), threshold=3)`.
    let percent = (amount * 200.0) as i32;
    let sharpened = image_edit::unsharp_mask(&rgba, 2.0, percent, 3);
    // Python re-saves RGB; to match the {"image": png} shape we PNG-encode the
    // RGBA buffer (alpha stays 255 from the RGB→RGBA lift, visually identical).
    let b64 = image_edit::encode_png_b64(&image::DynamicImage::ImageRgba8(sharpened))
        .map_err(|e| HttpException::new(500, format!("Internal Server Error: {e}")))?;
    Ok(Json(json!({ "image": b64 })).into_response())
}

/// `denoise_image(request)` — REAL port of `gallery_routes.py:1295-1343`.
///
/// `strength = clamp(float(body.get("strength", 0.5)), 0, 1)`, decode the image as
/// RGB, run Real-ESRGAN `realesr-general-x4v3` at outscale=1. Returns `{"image":
/// base64-png}` on success, `{"error": ...}` (HTTP 200) when the model can't be
/// obtained or a run fails — NEVER a 500. Missing input → HTTPException(400).
///
/// DNI DRIFT (documented, unavoidable): Python passes `dni_weight=[strength,
/// 1-strength]`, which interpolates `realesr-general-x4v3` with the companion
/// `realesr-general-wdn-x4v3` checkpoint to dial denoise strength. A single ONNX
/// graph cannot reproduce that two-model interpolation, so `strength` is accepted
/// best-effort and the delivered behavior is the `realesr-general-x4v3` base at
/// outscale=1 (see `image_models::realesr_general_denoise`). NOT faked, NOT a stub.
async fn denoise_image(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    connect_info: Option<ConnectInfo<SocketAddr>>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    // `require_privilege(request, "can_generate_images")` (gallery_routes.py:1338).
    require_can_generate_images(user, &s, connect_info)?;
    let body: Value = serde_json::from_slice(&raw_body).unwrap_or(Value::Null);
    let image_b64 = body.get("image").and_then(|v| v.as_str()).unwrap_or("");
    // `if not image_b64: raise HTTPException(400, "No image provided")`.
    if image_b64.is_empty() {
        return Err(HttpException::new(400, "No image provided"));
    }
    // `strength = max(0.0, min(1.0, float(body.get("strength", 0.5))))`.
    let strength = (coerce_f64(body.get("strength"), 0.5)).clamp(0.0, 1.0) as f32;
    // `src = Image.open(...).convert("RGB")` — a decode failure here in Python
    // raises (no guard around b64decode) → 500. Mirror that.
    let decoded = image_edit::decode_image_b64(image_b64)
        .map_err(|e| HttpException::new(500, format!("Internal Server Error: {e}")))?;
    let rgba = decoded.to_rgba8();
    // Heavy ort run off the async worker (Session::run is sync).
    let out = tokio::task::spawn_blocking(move || {
        image_models::realesr_general_denoise(&rgba, strength)
    })
    .await
    .unwrap_or_else(|e| Err(format!("Denoise failed: {e}")));
    match out {
        Ok(img) => {
            let b64 = image_edit::encode_png_b64(&image::DynamicImage::ImageRgba8(img))
                .unwrap_or_default();
            Ok(Json(json!({ "image": b64 })).into_response())
        }
        // `return {"error": ...}` — soft-fail (HTTP 200), never a 500.
        Err(msg) => Ok(Json(json!({ "error": msg })).into_response()),
    }
}

/// `upscale_image_local(request)` — REAL port of `gallery_routes.py:1345-1389`.
///
/// `scale = body.get("scale", 2)` forced to {2,4}, decode RGB, run Real-ESRGAN
/// `RealESRGAN_x4plus` tiled (tile=400, pad=10) and downscale to the requested
/// outscale. Returns `{"image": base64-png}`, or `{"error": ...}` (HTTP 200) when
/// the model can't be obtained / a run fails. Missing input → HTTPException(400).
async fn upscale_image_local(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    connect_info: Option<ConnectInfo<SocketAddr>>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    // `require_privilege(request, "can_generate_images")` (gallery_routes.py:1388).
    require_can_generate_images(user, &s, connect_info)?;
    let body: Value = serde_json::from_slice(&raw_body).unwrap_or(Value::Null);
    let image_b64 = body.get("image").and_then(|v| v.as_str()).unwrap_or("");
    if image_b64.is_empty() {
        return Err(HttpException::new(400, "No image provided"));
    }
    // `scale = int(body.get("scale", 2)); scale = 2 if scale not in (2,4) else scale`.
    let scale_in = coerce_f64(body.get("scale"), 2.0) as i64;
    let scale: u32 = if scale_in == 2 || scale_in == 4 {
        scale_in as u32
    } else {
        2
    };
    let decoded = image_edit::decode_image_b64(image_b64)
        .map_err(|e| HttpException::new(500, format!("Internal Server Error: {e}")))?;
    let rgba = decoded.to_rgba8();
    let out = tokio::task::spawn_blocking(move || image_models::realesrgan_upscale(&rgba, scale))
        .await
        .unwrap_or_else(|e| Err(format!("Upscale failed: {e}")));
    match out {
        Ok(img) => {
            let b64 = image_edit::encode_png_b64(&image::DynamicImage::ImageRgba8(img))
                .unwrap_or_default();
            Ok(Json(json!({ "image": b64 })).into_response())
        }
        Err(msg) => Ok(Json(json!({ "error": msg })).into_response()),
    }
}

/// `remove_background(request)` — REAL port of `gallery_routes.py:1391-1481`,
/// INCLUDING the full hint_mask crop→run→paste-back→alpha-multiply logic.
///
/// Decode source → RGBA (W×H). If a `hint_mask` is present: decode → L, resize
/// NEAREST to W×H if differing, take the bounding box of non-zero pixels with 8px
/// padding (clamped to the image), crop to it. Run u2net saliency on the crop
/// (`image_models::u2net_salient_mask`) → apply as the crop's alpha → paste back
/// into a transparent W×H canvas at the bbox offset. If a hint was supplied,
/// multiply the final alpha by the hint (`image_edit::multiply_luma`) so anything
/// outside the hint is forced transparent. Returns `{"image": base64-png}`, or
/// `{"error": "No background removal model available. ..."}` (HTTP 200, py:1457)
/// when the model can't be obtained.
async fn remove_background(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    connect_info: Option<ConnectInfo<SocketAddr>>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    // `require_privilege(request, "can_generate_images")` (gallery_routes.py:1445).
    require_can_generate_images(user, &s, connect_info)?;
    let body: Value = serde_json::from_slice(&raw_body).unwrap_or(Value::Null);
    // `img_bytes = base64.b64decode(image_b64)` — no guard; failure → 500.
    let image_b64 = body.get("image").and_then(|v| v.as_str()).unwrap_or("");
    let decoded = image_edit::decode_image_b64(image_b64)
        .map_err(|e| HttpException::new(500, format!("Internal Server Error: {e}")))?;
    let img = decoded.to_rgba8();
    let (w, h) = (img.width(), img.height());

    // `hint = None; bbox = None; if hint_b64: try: ...` — any failure inside the
    // try resets hint/bbox to None (py:1417-1436).
    let hint_b64 = body.get("hint_mask").and_then(|v| v.as_str());
    let mut hint: Option<image::GrayImage> = None;
    let mut bbox: Option<(u32, u32, u32, u32)> = None; // (x0, y0, x1, y1) exclusive-right
    if let Some(hb) = hint_b64.filter(|s| !s.is_empty()) {
        if let Ok(hdec) = image_edit::decode_image_b64(hb) {
            let mut hl = hdec.to_luma8();
            // `if hint.size != img.size: hint = hint.resize(img.size, Image.NEAREST)`.
            if hl.width() != w || hl.height() != h {
                hl = image::imageops::resize(&hl, w, h, image::imageops::FilterType::Nearest);
            }
            // `bbox = hint.getbbox()` — bounding box of non-zero pixels.
            let bb = luma_getbbox(&hl);
            hint = Some(hl);
            if let Some((x0, y0, x1, y1)) = bb {
                // `pad = 8; bbox = (max(0,x0-8), max(0,y0-8), min(W,x1+8), min(H,y1+8))`.
                let pad = 8i64;
                let nx0 = (x0 as i64 - pad).max(0) as u32;
                let ny0 = (y0 as i64 - pad).max(0) as u32;
                let nx1 = ((x1 as i64) + pad).min(w as i64) as u32;
                let ny1 = ((y1 as i64) + pad).min(h as i64) as u32;
                bbox = Some((nx0, ny0, nx1, ny1));
            }
        }
    }

    // `crop = img.crop(bbox) if bbox else img`.
    let (crop, off_x, off_y) = match bbox {
        Some((x0, y0, x1, y1)) => {
            let cw = x1.saturating_sub(x0).max(1);
            let ch = y1.saturating_sub(y0).max(1);
            let sub = image::imageops::crop_imm(&img, x0, y0, cw, ch).to_image();
            (sub, x0, y0)
        }
        None => (img.clone(), 0, 0),
    };

    // Run u2net on the crop (heavy → spawn_blocking). Err → the py:1457 soft-fail.
    let crop_for_run = crop.clone();
    let mask = tokio::task::spawn_blocking(move || image_models::u2net_salient_mask(&crop_for_run))
        .await
        .unwrap_or_else(|e| Err(format!("background removal failed: {e}")));
    let mask = match mask {
        Ok(m) => m,
        Err(_msg) => {
            // py:1457 exact string.
            return Ok(Json(json!({
                "error": "No background removal model available. Install rembg: pip install rembg"
            }))
            .into_response());
        }
    };

    // `cut = remove(crop)` → apply the saliency mask as the crop's alpha.
    let mut cut = crop;
    apply_alpha(&mut cut, &mask);

    // Compose back into a full-size transparent canvas (py:1459-1464).
    let mut result: image::RgbaImage = match bbox {
        Some(_) => {
            let mut canvas = image::RgbaImage::from_pixel(w, h, image::Rgba([0, 0, 0, 0]));
            image::imageops::overlay(&mut canvas, &cut, off_x as i64, off_y as i64);
            canvas
        }
        None => cut,
    };

    // `if hint is not None: a = ImageChops.multiply(a, hint); merge` (py:1468-1473).
    if let Some(h_mask) = hint {
        let alpha = extract_alpha(&result);
        let multiplied = image_edit::multiply_luma(&alpha, &h_mask);
        apply_alpha(&mut result, &multiplied);
    }

    let b64 = image_edit::encode_png_b64(&image::DynamicImage::ImageRgba8(result))
        .map_err(|e| HttpException::new(500, format!("Internal Server Error: {e}")))?;
    Ok(Json(json!({ "image": b64 })).into_response())
}

/// `enhance_face(request)` — REAL port of `gallery_routes.py:1483-1546`.
///
/// Decode RGB, attempt GFPGAN-512 ONNX restore (`image_models::gfpgan_restore`).
/// On success returns `{"image": base64-png}` with NO `method` field (py:1530's
/// GFPGAN-success shape). When the model can't be obtained or the run fails, falls
/// back to the PIL ImageEnhance chain (`image_edit::pil_enhance_fallback`) and
/// returns `{"image": ..., "method": "pil"}` (py:1532-1544 — Python's own
/// ImportError branch). Missing input → HTTPException(400).
///
/// SCOPE REDUCTION (documented): the full GFPGANer pipeline = retinaface detect +
/// 5-landmark align/warp + 512 restore + inverse-warp paste-back + bg upsample.
/// Faithfully porting detect/align needs a second detector ONNX + warp math, so
/// the delivered behavior is a real GFPGAN-512 restore on the WHOLE image (resize
/// → restore → resize back). On multi-face / off-center photos this differs from
/// the Python paste-back result, but it is a genuine GFPGAN restore, NOT faked.
async fn enhance_face(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    connect_info: Option<ConnectInfo<SocketAddr>>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    // `require_privilege(request, "can_generate_images")` (gallery_routes.py:1527).
    require_can_generate_images(user, &s, connect_info)?;
    let body: Value = serde_json::from_slice(&raw_body).unwrap_or(Value::Null);
    let image_b64 = body.get("image").and_then(|v| v.as_str()).unwrap_or("");
    if image_b64.is_empty() {
        return Err(HttpException::new(400, "No image provided"));
    }
    // `img = Image.open(...).convert("RGB")` — a decode failure raises in Python
    // (no guard around b64decode) → 500.
    let decoded = image_edit::decode_image_b64(image_b64)
        .map_err(|e| HttpException::new(500, format!("Internal Server Error: {e}")))?;
    let rgba = decoded.to_rgba8();

    // Try GFPGAN first (heavy → spawn_blocking).
    let rgba_for_run = rgba.clone();
    let restored = tokio::task::spawn_blocking(move || image_models::gfpgan_restore(&rgba_for_run))
        .await
        .unwrap_or_else(|e| Err(format!("Face enhancement failed: {e}")));
    match restored {
        // `return {"image": ...}` — GFPGAN success: image-only, NO method (py:1530).
        Ok(img) => {
            let b64 = image_edit::encode_png_b64(&image::DynamicImage::ImageRgba8(img))
                .map_err(|e| HttpException::new(500, format!("Internal Server Error: {e}")))?;
            Ok(Json(json!({ "image": b64 })).into_response())
        }
        // GFPGAN unavailable / run failed → PIL ImageEnhance fallback (py:1532-1544).
        Err(_msg) => {
            crate::pylog::info("GFPGAN not available — using PIL enhancement fallback");
            let enhanced = image_edit::pil_enhance_fallback(&rgba);
            let b64 = image_edit::encode_png_b64(&image::DynamicImage::ImageRgba8(enhanced))
                .map_err(|e| HttpException::new(500, format!("Internal Server Error: {e}")))?;
            // `return {"image": ..., "method": "pil"}`.
            Ok(Json(json!({ "image": b64, "method": "pil" })).into_response())
        }
    }
}

/// `Image.getbbox()` for an L plane — the smallest box `(x0, y0, x1, y1)` (with
/// `x1`/`y1` EXCLUSIVE, matching PIL) containing every non-zero pixel, or `None`
/// when the plane is entirely zero.
fn luma_getbbox(img: &image::GrayImage) -> Option<(u32, u32, u32, u32)> {
    let (w, h) = (img.width(), img.height());
    let (mut min_x, mut min_y, mut max_x, mut max_y) = (u32::MAX, u32::MAX, 0u32, 0u32);
    let mut found = false;
    for y in 0..h {
        for x in 0..w {
            if img.get_pixel(x, y).0[0] != 0 {
                found = true;
                if x < min_x {
                    min_x = x;
                }
                if y < min_y {
                    min_y = y;
                }
                if x > max_x {
                    max_x = x;
                }
                if y > max_y {
                    max_y = y;
                }
            }
        }
    }
    if found {
        // PIL's bbox right/bottom are exclusive (max index + 1).
        Some((min_x, min_y, max_x + 1, max_y + 1))
    } else {
        None
    }
}

/// `img.putalpha(mask)` — set the RGBA image's alpha plane from an L plane of the
/// SAME dimensions. The mask is resized NEAREST to the image dims if they differ
/// (defensive; the callers already match dims).
fn apply_alpha(img: &mut image::RgbaImage, mask: &image::GrayImage) {
    let (w, h) = (img.width(), img.height());
    let resized;
    let m: &image::GrayImage = if mask.width() == w && mask.height() == h {
        mask
    } else {
        resized = image::imageops::resize(mask, w, h, image::imageops::FilterType::Nearest);
        &resized
    };
    for y in 0..h {
        for x in 0..w {
            img.get_pixel_mut(x, y).0[3] = m.get_pixel(x, y).0[0];
        }
    }
}

/// `_, _, _, a = img.split()` — extract the alpha plane as an L image.
fn extract_alpha(img: &image::RgbaImage) -> image::GrayImage {
    let (w, h) = (img.width(), img.height());
    let mut out = image::GrayImage::new(w, h);
    for y in 0..h {
        for x in 0..w {
            out.put_pixel(x, y, image::Luma([img.get_pixel(x, y).0[3]]));
        }
    }
    out
}

// ===========================================================================
// Shared helpers
// ===========================================================================

/// `user = get_current_user(request)` — the username, or `None` (auth disabled).
fn current_user(user: Option<Extension<CurrentUser>>) -> Option<String> {
    user.map(|Extension(CurrentUser(u))| u)
}

/// `require_privilege(request, "can_generate_images")` — the privilege gate the
/// image-edit endpoints (ai-upscale / style-transfer / inpaint / harmonize /
/// denoise / upscale-local / remove-bg / enhance-face) run at their top. Bridges
/// the axum `Extension<CurrentUser>` + `ConnectInfo` to the ported
/// [`crate::routes::auth_adapter::require_privilege`], which raises
/// `HTTPException(403, ...)` when the caller's `can_generate_images` flag is False
/// (and `HTTPException(401, "Not authenticated")` when unauthenticated). The
/// handlers ignore the returned username (Python likewise just calls it for the
/// side effect), so this returns `()`.
fn require_can_generate_images(
    user: Option<Extension<CurrentUser>>,
    state: &AppState,
    connect_info: Option<ConnectInfo<SocketAddr>>,
) -> Result<(), HttpException> {
    let user_opt: Option<String> = current_user(user);
    let client_host = connect_info.map(|ConnectInfo(a)| a.ip().to_string());
    crate::routes::auth_adapter::require_privilege(
        user_opt.as_deref(),
        state,
        client_host.as_deref(),
        "can_generate_images",
    )
    .map(|_| ())
}

/// `if not user or row.owner != user` — the strict owner-scope check used by the
/// per-image / per-album handlers. A `None` user fails the check (`not user`), and
/// a non-matching owner fails too. Returns `Ok(true)` only when `user` is present
/// AND the row's owner equals it.
fn owns_image(
    conn: &Connection,
    image_id: &str,
    user: Option<&str>,
) -> Result<bool, HttpException> {
    let owner: Option<Option<String>> = conn
        .query_row(
            "SELECT owner FROM gallery_images WHERE id = ?1",
            rusqlite::params![image_id],
            |r| r.get::<_, Option<String>>(0),
        )
        .optional()
        .map_err(db_500)?;
    Ok(match (user, owner) {
        (Some(u), Some(o)) => o.as_deref() == Some(u),
        _ => false,
    })
}

/// `db.query(GalleryImage).filter(id == image_id).first()` existence check.
fn image_exists(conn: &Connection, image_id: &str) -> Result<bool, HttpException> {
    let n: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM gallery_images WHERE id = ?1",
            rusqlite::params![image_id],
            |r| r.get(0),
        )
        .map_err(db_500)?;
    Ok(n > 0)
}

/// `_get_or_404_image(db, image_id, user)` — fetch a full image row; 404 if the
/// row is missing OR `not user or img.owner != user`.
fn get_or_404_image(
    conn: &Connection,
    image_id: &str,
    user: Option<&str>,
) -> Result<GalleryImage, HttpException> {
    let img: Option<GalleryImage> = conn
        .query_row(
            &format!("SELECT {IMG_COLS} FROM gallery_images WHERE id = ?1"),
            rusqlite::params![image_id],
            read_image,
        )
        .optional()
        .map_err(db_500)?;
    let img = match img {
        Some(i) => i,
        None => return Err(HttpException::new(404, "Image not found")),
    };
    if !owns_image(conn, image_id, user)? {
        return Err(HttpException::new(404, "Image not found"));
    }
    Ok(img)
}

/// `_get_or_404_album(db, album_id, user)` — 404 if missing OR `not user or
/// album.owner != user`.
fn get_or_404_album(
    conn: &Connection,
    album_id: &str,
    user: Option<&str>,
) -> Result<(), HttpException> {
    let owner: Option<Option<String>> = conn
        .query_row(
            "SELECT owner FROM gallery_albums WHERE id = ?1",
            rusqlite::params![album_id],
            |r| r.get::<_, Option<String>>(0),
        )
        .optional()
        .map_err(db_500)?;
    let owner = match owner {
        Some(o) => o,
        None => return Err(HttpException::new(404, "Album not found")),
    };
    let ok = match (user, owner) {
        (Some(u), Some(o)) => o.as_str() == u,
        _ => false,
    };
    if !ok {
        return Err(HttpException::new(404, "Album not found"));
    }
    Ok(())
}

/// A configured image endpoint row (first enabled `model_type == "image"`).
struct ImageEndpoint {
    base_url: String,
    api_key: Option<String>,
}

/// `db.query(ModelEndpoint).filter(model_type == "image", is_enabled == True).first()`,
/// with the `EncryptedText` api_key decrypted.
fn first_image_endpoint() -> Result<Option<ImageEndpoint>, HttpException> {
    let conn = session_local()?;
    let row: Option<(String, Option<String>)> = conn
        .query_row(
            "SELECT base_url, api_key FROM model_endpoints \
             WHERE model_type = 'image' AND is_enabled = 1 LIMIT 1",
            [],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()
        .map_err(db_500)?;
    Ok(row.map(|(base_url, enc)| ImageEndpoint {
        base_url,
        api_key: decrypt_key(enc),
    }))
}

/// Decrypt the `EncryptedText` api_key (empty/NULL → None).
fn decrypt_key(enc: Option<String>) -> Option<String> {
    enc.filter(|k| !k.is_empty())
        .map(|k| crate::src::secret_storage::decrypt(&k))
}

/// `_norm_url(u)` for inpaint: strip trailing `/`, then a trailing `/v1`. Used to
/// find the matching `model_endpoints` row's decrypted api_key.
fn api_key_for_normalized_base(base: &str) -> Option<String> {
    let target = norm_url(base);
    let conn = session_local().ok()?;
    let mut stmt = conn
        .prepare("SELECT base_url, api_key FROM model_endpoints")
        .ok()?;
    let rows = stmt
        .query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, Option<String>>(1)?,
            ))
        })
        .ok()?;
    for row in rows.flatten() {
        if norm_url(&row.0) == target {
            return decrypt_key(row.1);
        }
    }
    None
}

/// `_norm_url`: `u.rstrip("/")` then drop a trailing `/v1`.
fn norm_url(u: &str) -> String {
    if u.is_empty() {
        return u.to_string();
    }
    let u = u.trim_end_matches('/');
    u.strip_suffix("/v1").unwrap_or(u).to_string()
}

/// harmonize's api_key match (gallery_routes.py:1161, post-fix):
/// `ep.base_url.rstrip("/").removesuffix("/v1").rstrip("/")
///   == base.rstrip("/").removesuffix("/v1").rstrip("/")`.
///
/// FIX: the old code used Python's `str.rstrip("/v1")`, which strips any trailing
/// char in the set `{/, v, 1}` — a long-standing bug that also chewed a host like
/// `host11` down to `host`. The corrected form drops exactly one trailing `/v1`
/// SUFFIX (via `removesuffix`), bracketed by `rstrip("/")` to absorb trailing
/// slashes. `removesuffix_v1` reproduces that suffix-only strip.
fn api_key_for_rstrip_v1(base: &str) -> Option<String> {
    let target = norm_url_removesuffix(base);
    let conn = session_local().ok()?;
    let mut stmt = conn
        .prepare("SELECT base_url, api_key FROM model_endpoints")
        .ok()?;
    let rows = stmt
        .query_map([], |r| {
            Ok((r.get::<_, String>(0)?, r.get::<_, Option<String>>(1)?))
        })
        .ok()?;
    for row in rows.flatten() {
        if norm_url_removesuffix(&row.0) == target {
            return decrypt_key(row.1);
        }
    }
    None
}

/// `u.rstrip("/").removesuffix("/v1").rstrip("/")` — strip trailing slashes, then a
/// single trailing `/v1` SUFFIX (NOT a char-set), then any slash the suffix-strip
/// exposed. Used by harmonize's api_key lookup (gallery_routes.py:1161).
fn norm_url_removesuffix(u: &str) -> String {
    let s = u.trim_end_matches('/');
    let s = s.strip_suffix("/v1").unwrap_or(s);
    s.trim_end_matches('/').to_string()
}

/// `base += "/v1"` unless it already ends with `/v1`.
fn ensure_v1(base: &str) -> String {
    if base.ends_with("/v1") {
        base.to_string()
    } else {
        format!("{base}/v1")
    }
}

/// `body.pop(key, "")` — remove and return the string value (empty when absent or
/// not a string). The `or ""` truthiness is applied by the caller where needed.
fn pop_str(body: &mut Value, key: &str) -> String {
    if let Value::Object(map) = body {
        match map.remove(key) {
            Some(Value::String(s)) => s,
            _ => String::new(),
        }
    } else {
        String::new()
    }
}

/// `float(x)` with a default on failure — Python `try: float(...) except: default`.
fn coerce_f64(v: Option<&Value>, default: f64) -> f64 {
    match v {
        Some(Value::Number(n)) => n.as_f64().unwrap_or(default),
        Some(Value::String(s)) => s.trim().parse::<f64>().unwrap_or(default),
        Some(Value::Bool(b)) => {
            if *b {
                1.0
            } else {
                0.0
            }
        }
        _ => default,
    }
}

/// Merge the keys of `extra` (an object) into `target` (an object), like Python
/// `{**a, **b}` dict-unpack used in the harmonize candidate payloads.
fn merge_obj(target: &mut Value, extra: Value) {
    if let (Value::Object(t), Value::Object(e)) = (target, extra) {
        for (k, v) in e {
            t.insert(k, v);
        }
    }
}

/// Standard (non-url-safe) base64 encode — `base64.b64encode(...).decode()`.
fn base64_std(bytes: &[u8]) -> String {
    use base64::Engine;
    base64::engine::general_purpose::STANDARD.encode(bytes)
}

/// `hashlib.sha256(content).hexdigest()`.
fn sha256_hex(content: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let mut h = Sha256::new();
    h.update(content);
    let digest = h.finalize();
    let mut out = String::with_capacity(64);
    for b in digest {
        out.push_str(&format!("{b:02x}"));
    }
    out
}

/// `os.path.splitext(name)[1]` — the extension WITH its leading dot, or "".
fn splitext_ext(name: &str) -> String {
    // Python's splitext: the last dot not at the start of the basename.
    let base = name.rsplit('/').next().unwrap_or(name);
    match base.rfind('.') {
        Some(i) if i > 0 => name[name.len() - (base.len() - i)..].to_string(),
        _ => String::new(),
    }
}

/// `os.path.splitext(name)[0]` — the path/name without its extension.
fn splitext_stem(name: &str) -> String {
    let ext = splitext_ext(name);
    if ext.is_empty() {
        name.to_string()
    } else {
        name[..name.len() - ext.len()].to_string()
    }
}

/// `re.sub(r"[^\w\-. ]+", "", base)` — drop any run of chars outside `[\w\-. ]`.
/// Python `\w` is `[A-Za-z0-9_]` plus Unicode word chars; we use `char::is_alphanumeric`
/// (Unicode-aware) plus `_`, `-`, `.`, and space, matching the intent.
fn sanitize_arcname(base: &str) -> String {
    base.chars()
        .filter(|c| c.is_alphanumeric() || *c == '_' || *c == '-' || *c == '.' || *c == ' ')
        .collect()
}

/// `_sanitize_gallery_filename(filename)` — return a local filename safe to join
/// under `generated_images` (gallery_routes.py:24-29):
/// ```python
/// safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename or "").name)[:128]
/// if not safe_name or safe_name in {".", ".."}:
///     safe_name = uuid.uuid4().hex[:12]
/// return safe_name
/// ```
/// `Path(...).name` is the final path component (after the last `/`). Each char
/// outside the ASCII set `[A-Za-z0-9._-]` is replaced with `_` (per-char, like
/// `re.sub` with a 1-char class), the result is truncated to 128 chars, and the
/// empty / `"."` / `".."` cases fall back to a random 12-hex-char name. This blocks
/// path traversal (`/`, `..`) and absolute paths in a DB-stored `filename`.
fn sanitize_gallery_filename(filename: &str) -> String {
    // `Path(filename or "").name` — the final path component. `pathlib` strips any
    // trailing separators first (`Path("foo/").name == "foo"`), then takes the part
    // after the last separator. We mirror that: trim trailing `/`/`\`, then split.
    // (Pure "/" or "" yields "" -> the fallback below.)
    let trimmed = filename.trim_end_matches(['/', '\\']);
    let name = trimmed.rsplit(['/', '\\']).next().unwrap_or("");
    // `re.sub(r"[^A-Za-z0-9._-]", "_", name)` — replace each disallowed char with `_`.
    let replaced: String = name
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '.' || c == '_' || c == '-' {
                c
            } else {
                '_'
            }
        })
        .collect();
    // `[:128]` — Python slices by code points.
    let safe: String = replaced.chars().take(128).collect();
    // `if not safe_name or safe_name in {".", ".."}: safe_name = uuid4().hex[:12]`.
    if safe.is_empty() || safe == "." || safe == ".." {
        let hex = uuid::Uuid::new_v4().simple().to_string();
        hex[..12].to_string()
    } else {
        safe
    }
}

/// `dt.isoformat() if dt else None` (plain isoformat, no Z), or null.
fn iso_or_null(stored: Option<&str>) -> Value {
    match stored.filter(|s| !s.is_empty()) {
        Some(s) => json!(crate::pydatetime::to_isoformat(s)),
        None => Value::Null,
    }
}

/// `x or None` for a string query/text value — None/"" → None.
fn non_empty(v: Option<String>) -> Option<String> {
    v.filter(|s| !s.is_empty())
}

/// `x or None` over an `Option<&String>` borrow (query map values).
fn non_empty_str(v: Option<&String>) -> Option<String> {
    v.filter(|s| !s.is_empty()).cloned()
}

/// FastAPI/Pydantic bool query coercion: truthy strings → true; "0"/"false"/etc → false.
fn parse_bool(v: Option<&String>) -> Option<bool> {
    let s = v?.trim().to_lowercase();
    match s.as_str() {
        "1" | "true" | "yes" | "on" | "t" | "y" => Some(true),
        "0" | "false" | "no" | "off" | "f" | "n" | "" => Some(false),
        _ => Some(false),
    }
}

/// Python `bool(value)` over a JSON value (used for `or`-style truthiness checks).
fn json_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `str(e)` for a reqwest transport error (best-effort match to httpx's str()).
fn reqwest_err(e: &reqwest::Error) -> String {
    e.to_string()
}

/// Open a DB connection (`SessionLocal()`), mapping a failure to a 500.
fn session_local() -> Result<Connection, HttpException> {
    crate::core::database::session_local().map_err(db_500)
}

/// Map a `rusqlite::Error` to a 500 `HttpException`.
fn db_500(e: rusqlite::Error) -> HttpException {
    crate::pylog::error(&format!("gallery_routes DB error: {e}"));
    HttpException::new(500, "Internal Server Error")
}

// ===========================================================================
// Multipart form collection
// ===========================================================================

/// A collected multipart form: text fields + file parts (name → (filename, bytes)).
#[derive(Debug)]
struct MultipartForm {
    texts: std::collections::HashMap<String, String>,
    files: std::collections::HashMap<String, (Option<String>, Vec<u8>)>,
}

impl MultipartForm {

    /// Like [`collect`] but applies a hard byte cap to every file field via
    /// [`upload_limits::read_upload_limited`].
    ///
    /// Port of commit 193dc2f ("fix(uploads): bound direct upload reads"):
    /// ```python
    /// content = await read_upload_limited(file, limit, label)
    /// ```
    /// Text fields are collected without a cap (they are small by definition).
    /// A single over-limit file field causes the entire form collection to fail
    /// with `HttpException(413, ...)` — same as Python's early `raise`.
    async fn collect_bounded(
        mut mp: Multipart,
        file_limit: usize,
        label: &str,
    ) -> Result<Self, HttpException> {
        let mut texts = std::collections::HashMap::new();
        let mut files = std::collections::HashMap::new();
        while let Ok(Some(field)) = mp.next_field().await {
            let name = field.name().unwrap_or("").to_string();
            if name.is_empty() {
                // still consume the body to advance the stream
                let _ = field.bytes().await;
                continue;
            }
            let filename = field.file_name().map(str::to_string);
            if filename.is_some() {
                // Apply the upload size cap via the shared helper (commit 193dc2f).
                let bytes = upload_limits::read_upload_limited(field, file_limit, label).await?;
                files.insert(name, (filename, bytes));
            } else if let Ok(text) = field.text().await {
                texts.insert(name, text);
            }
        }
        Ok(MultipartForm { texts, files })
    }

    /// `form.get("<name>")` for a text field.
    fn text(&self, name: &str) -> Option<String> {
        self.texts.get(name).cloned()
    }

    /// `form.get("<name>")` for a file field → `(filename, bytes)`.
    fn file(&self, name: &str) -> Option<(Option<String>, Vec<u8>)> {
        self.files.get(name).cloned()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn sha256_matches_python_hexdigest() {
        // hashlib.sha256(b"").hexdigest()
        assert_eq!(
            sha256_hex(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        // hashlib.sha256(b"hello").hexdigest()
        assert_eq!(
            sha256_hex(b"hello"),
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        );
    }

    #[test]
    fn splitext_matches_python() {
        // os.path.splitext("a.png") -> ("a", ".png")
        assert_eq!(splitext_ext("a.png"), ".png");
        assert_eq!(splitext_stem("a.png"), "a");
        // os.path.splitext("archive.tar.gz") -> ("archive.tar", ".gz")
        assert_eq!(splitext_ext("archive.tar.gz"), ".gz");
        assert_eq!(splitext_stem("archive.tar.gz"), "archive.tar");
        // no extension
        assert_eq!(splitext_ext("noext"), "");
        assert_eq!(splitext_stem("noext"), "noext");
        // dotfile: splitext(".bashrc") -> (".bashrc", "")
        assert_eq!(splitext_ext(".bashrc"), "");
        assert_eq!(splitext_stem(".bashrc"), ".bashrc");
    }

    #[test]
    fn sanitize_arcname_drops_unsafe_chars() {
        // re.sub(r"[^\w\-. ]+", "", base)
        assert_eq!(sanitize_arcname("a/b:c*d?"), "abcd");
        assert_eq!(sanitize_arcname("My Photo-1.png"), "My Photo-1.png");
        assert_eq!(sanitize_arcname("foo_bar baz"), "foo_bar baz");
        assert_eq!(sanitize_arcname("###"), "");
    }

    #[test]
    fn sanitize_gallery_filename_matches_python() {
        // Plain allowed names pass through unchanged (only the basename survives,
        // each char outside [A-Za-z0-9._-] -> "_").
        assert_eq!(sanitize_gallery_filename("abc123.png"), "abc123.png");
        assert_eq!(sanitize_gallery_filename("a-b_c.1.PNG"), "a-b_c.1.PNG");
        // `Path(...).name` keeps only the final component (drops directories), so a
        // traversal attempt can't escape the dir — the leading path is discarded.
        assert_eq!(sanitize_gallery_filename("../../etc/passwd"), "passwd");
        assert_eq!(sanitize_gallery_filename("/abs/evil.png"), "evil.png");
        assert_eq!(sanitize_gallery_filename("dir\\win.png"), "win.png");
        // Disallowed chars in the basename become "_": spaces, %, $, etc.
        assert_eq!(sanitize_gallery_filename("a b%c$.png"), "a_b_c_.png");
        // Unicode (outside ASCII alnum) -> one "_" per char (é is a single
        // code point, like Python's str). re.sub("café.png") -> "caf_.png".
        assert_eq!(sanitize_gallery_filename("café.png"), "caf_.png");
        // Empty basename / "." / ".." -> a random 12-hex fallback (length + hex).
        let fb = sanitize_gallery_filename("");
        assert_eq!(fb.len(), 12);
        assert!(fb.chars().all(|c| c.is_ascii_hexdigit()));
        // `Path("foo/").name == "foo"` (trailing slash dropped), not empty.
        assert_eq!(sanitize_gallery_filename("foo/"), "foo");
        // A bare "." basename triggers the fallback (Path(".").name == ".").
        let fb_dot = sanitize_gallery_filename(".");
        assert_eq!(fb_dot.len(), 12);
        // 128-char truncation.
        let long = "a".repeat(200);
        assert_eq!(sanitize_gallery_filename(&long).len(), 128);
    }

    #[test]
    fn norm_url_strips_slash_and_v1() {
        assert_eq!(norm_url("http://h:8000/v1/"), "http://h:8000");
        assert_eq!(norm_url("http://h:8000/v1"), "http://h:8000");
        assert_eq!(norm_url("http://h:8000/"), "http://h:8000");
        assert_eq!(norm_url("http://h:8000"), "http://h:8000");
    }

    #[test]
    fn ensure_v1_appends_once() {
        assert_eq!(ensure_v1("http://h/v1"), "http://h/v1");
        assert_eq!(ensure_v1("http://h"), "http://h/v1");
    }

    #[test]
    fn coerce_f64_python_float_semantics() {
        assert_eq!(coerce_f64(Some(&json!(0.5)), 0.45), 0.5);
        assert_eq!(coerce_f64(Some(&json!("0.7")), 0.45), 0.7);
        assert_eq!(coerce_f64(Some(&json!("bad")), 0.45), 0.45);
        assert_eq!(coerce_f64(None, 0.45), 0.45);
    }

    #[test]
    fn json_truthy_matches_python_bool() {
        assert!(!json_truthy(&Value::Null));
        assert!(!json_truthy(&json!("")));
        assert!(json_truthy(&json!("x")));
        assert!(!json_truthy(&json!(0)));
        assert!(json_truthy(&json!(1)));
        assert!(!json_truthy(&json!([])));
        assert!(json_truthy(&json!([1])));
    }

    #[test]
    fn parse_bool_truthy_set() {
        assert_eq!(parse_bool(Some(&"true".to_string())), Some(true));
        assert_eq!(parse_bool(Some(&"1".to_string())), Some(true));
        assert_eq!(parse_bool(Some(&"0".to_string())), Some(false));
        assert_eq!(parse_bool(Some(&"false".to_string())), Some(false));
        assert_eq!(parse_bool(None), None);
    }

    #[test]
    fn pop_str_removes_key() {
        let mut body = json!({"_endpoint": "http://h", "image": "x"});
        assert_eq!(pop_str(&mut body, "_endpoint"), "http://h");
        // key removed
        assert!(body.get("_endpoint").is_none());
        assert!(body.get("image").is_some());
        // absent / non-string → ""
        assert_eq!(pop_str(&mut body, "missing"), "");
    }

    #[test]
    fn merge_obj_unpacks_like_python() {
        let mut target = json!({"a": 1});
        merge_obj(&mut target, json!({"model": "x"}));
        assert_eq!(target, json!({"a": 1, "model": "x"}));
        // empty extra is a no-op
        merge_obj(&mut target, json!({}));
        assert_eq!(target, json!({"a": 1, "model": "x"}));
    }

    #[test]
    fn norm_url_removesuffix_strips_suffix_not_charset() {
        // BUGFIX (gallery_routes.py:1161): the corrected normalization drops a
        // single trailing `/v1` SUFFIX, bracketed by `rstrip("/")` — NOT a
        // char-set strip of {/, v, 1}.
        // `"http://h:8000/v1/".rstrip("/").removesuffix("/v1").rstrip("/")`.
        assert_eq!(norm_url_removesuffix("http://h:8000/v1/"), "http://h:8000");
        assert_eq!(norm_url_removesuffix("http://h:8000/v1"), "http://h:8000");
        assert_eq!(norm_url_removesuffix("http://h:8000/"), "http://h:8000");
        assert_eq!(norm_url_removesuffix("http://h:8000"), "http://h:8000");
        // The OLD char-set bug would chew a host ending in v/1 down (e.g.
        // "http://host11" -> "http://host"); the fix MUST preserve it because
        // there's no `/v1` suffix to remove.
        assert_eq!(norm_url_removesuffix("http://host11"), "http://host11");
        assert_eq!(norm_url_removesuffix("http://myv1host"), "http://myv1host");
        // Only the LAST `/v1` suffix is removed, not an embedded one.
        assert_eq!(norm_url_removesuffix("http://h/v1/api"), "http://h/v1/api");
    }

    #[test]
    fn iso_or_null_plain_isoformat() {
        assert_eq!(iso_or_null(None), Value::Null);
        assert_eq!(iso_or_null(Some("")), Value::Null);
        assert_eq!(
            iso_or_null(Some("2026-06-01 12:30:00")),
            json!("2026-06-01T12:30:00")
        );
    }

    #[test]
    fn non_empty_truthiness() {
        assert_eq!(non_empty(Some("x".to_string())), Some("x".to_string()));
        assert_eq!(non_empty(Some("".to_string())), None);
        assert_eq!(non_empty(None), None);
    }

    #[test]
    fn int_to_bool_maps_sqlite_ints() {
        assert_eq!(int_to_bool(Some(1)), Some(true));
        assert_eq!(int_to_bool(Some(0)), Some(false));
        assert_eq!(int_to_bool(None), None);
    }

    // ── collect_bounded size-cap tests (commit 193dc2f) ─────────────────────
    //
    // Build a real multipart/form-data request (same helper as upload_limits
    // tests in src/upload_limits.rs) and verify that collect_bounded enforces
    // the cap via HttpException(413) and that under-limit uploads pass through.

    use axum::body::Body;
    use axum::extract::Multipart;
    use axum::http::Request;

    fn make_multipart_body_named(data: &[u8], field: &str) -> (String, Vec<u8>) {
        let boundary = "gallerytestbnd1234";
        let ct = format!("multipart/form-data; boundary={boundary}");
        let mut body = Vec::new();
        body.extend_from_slice(format!("--{boundary}\r\n").as_bytes());
        body.extend_from_slice(
            format!(
                "Content-Disposition: form-data; name=\"{field}\"; filename=\"img.bin\"\r\n"
            )
            .as_bytes(),
        );
        body.extend_from_slice(b"Content-Type: application/octet-stream\r\n\r\n");
        body.extend_from_slice(data);
        body.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());
        (ct, body)
    }

    async fn run_bounded(data: &[u8], limit: usize, label: &str) -> Result<MultipartForm, HttpException> {
        let (ct, body) = make_multipart_body_named(data, "file");
        let req = Request::builder()
            .header("content-type", ct)
            .body(Body::from(body))
            .unwrap();
        use axum::extract::FromRequest;
        let mp = Multipart::from_request(req, &()).await.unwrap();
        MultipartForm::collect_bounded(mp, limit, label).await
    }

    /// Under-limit file upload → Ok, bytes preserved.
    #[tokio::test]
    async fn collect_bounded_under_limit_ok() {
        let data = b"hello gallery";
        let form = run_bounded(data, 100, "Gallery upload").await.unwrap();
        let (_fname, bytes) = form.file("file").expect("file field present");
        assert_eq!(bytes, data);
    }

    /// Exactly at limit → Ok (len == limit is not over).
    #[tokio::test]
    async fn collect_bounded_at_limit_ok() {
        let data: Vec<u8> = vec![0xAB; 20];
        let form = run_bounded(&data, 20, "Gallery upload").await.unwrap();
        let (_fname, bytes) = form.file("file").expect("file field present");
        assert_eq!(bytes, data);
    }

    /// One byte over limit → HttpException 413 with "Gallery upload" label.
    #[tokio::test]
    async fn collect_bounded_over_limit_413() {
        let data: Vec<u8> = vec![0xFF; 21];
        let err = run_bounded(&data, 20, "Gallery upload").await.unwrap_err();
        assert_eq!(err.status_code, 413);
        assert!(
            err.detail.starts_with("Gallery upload exceeds"),
            "detail should start with label: {}",
            err.detail
        );
    }

    /// Transform label ("Image upload") appears in the 413 detail.
    #[tokio::test]
    async fn collect_bounded_transform_label_413() {
        // 1 MB cap (formatted "1 MB") keeps the body under axum's 2 MB default
        // Multipart limit; the live routes disable that limit (build_router) so
        // the real 25/100 MB caps apply. The bounding logic is identical.
        const CAP: usize = 1024 * 1024;
        let data: Vec<u8> = vec![0u8; CAP + 1];
        let err = run_bounded(&data, CAP, "Image upload").await.unwrap_err();
        assert_eq!(err.status_code, 413);
        assert!(
            err.detail.contains("Image upload"),
            "detail should contain label: {}",
            err.detail
        );
        assert!(
            err.detail.contains("1 MB"),
            "detail should contain '1 MB': {}",
            err.detail
        );
    }
}
