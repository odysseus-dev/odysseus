// routes/gallery_helpers.rs  <- routes/gallery_helpers.py
//! gallery_helpers.py — extracted helpers, models, and small utilities.
//!
//! Imported by `gallery_routes.py` (wave-6 DOCS). Faithful translation of the
//! Python module: the `GalleryPatch` request schema, `_image_to_dict` (an ordered
//! dict, key-for-key), `_owner_filter` (the SQLAlchemy WHERE-fragment, recast as a
//! raw-SQL fragment for the rusqlite route module), `_human_size`, and the real
//! `_extract_exif` port (PIL → `image` + `kamadak-exif`).
//!
//! ## `_extract_exif` is a REAL port (PIL → `image` + `kamadak-exif`)
//! The Python opens the image via Pillow (`from PIL import Image`) and reads
//! `width`/`height` plus the EXIF tag block (camera make/model, DateTimeOriginal,
//! GPS). Both halves are ported faithfully against the same in-memory bytes:
//! * `width`/`height` are read HEADER-ONLY via
//!   `image::ImageReader::with_guessed_format()?.into_dimensions()`, matching PIL's
//!   lazy `Image.open()` (which reads dimensions from the header without decoding
//!   the pixel body, since `_extract_exif` never calls `.load()`). A header/format
//!   failure reproduces Python's `except` branch: the dimensions stay `None` and
//!   `result["exif_error"] = str(e)` is set.
//! * The EXIF tags come from the pure-Rust `kamadak-exif` crate (imported as
//!   `exif`), which mirrors `Image._getexif()`:
//!     - `camera_make` / `camera_model` ← `Tag::Make` / `Tag::Model`, decoded to
//!       string then `.strip() or None` (set to the value, or `Null` when empty).
//!     - `taken_at` ← the first present of `DateTimeOriginal` / `DateTimeDigitized`
//!       / `DateTime`, parsed with Python's `"%Y:%m:%d %H:%M:%S"` and carried as the
//!       SQLAlchemy-serialized datetime string `"%Y-%m-%d %H:%M:%S.%6f"` (the form
//!       the route stores into the `taken_at` DateTime column — a Python `datetime`
//!       lands in SQLite with 6-digit microseconds, here always `.000000`).
//!     - `gps_lat` / `gps_lng` ← `GPSLatitude`/`GPSLongitude` (3 rationals → decimal
//!       degrees `d + m/60 + s/3600`), negated for a `S`/`W` ref, formatted
//!       `f"{x:.6f}"`. Keys use the Python names `gps_lat` / `gps_lng`.
//! As in the Python, a key is only set when the tag is present (Python truthiness),
//! and any EXIF read failure is swallowed (the bare `except Exception: pass` around
//! the GPS block, and the missing-EXIF early return). When the bytes carry no EXIF
//! at all, only `width`/`height` are returned — exactly the Python `if not exif:
//! return result` path.


use indexmap::IndexMap;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::pylog as logger;

// ---- Request schemas ----

/// `class GalleryPatch(BaseModel)` — the PATCH body for `/api/gallery/image/{id}`.
///
/// Every field is `Optional[...] = None`; `#[serde(default)]` reproduces the
/// "missing → None" Pydantic behavior (an absent key deserializes to `None`,
/// distinct from an explicit `null`, but both land as `None` here — matching
/// Pydantic, which treats both the same for an `Optional` default).
#[derive(Debug, Clone, Default, Deserialize)]
pub struct GalleryPatch {
    #[serde(default)]
    pub tags: Option<String>,
    #[serde(default)]
    pub favorite: Option<bool>,
    #[serde(default)]
    pub album_id: Option<String>,
}

// ---- GalleryImage row ----

/// The `GalleryImage` columns that `_image_to_dict` reads.
///
/// `core/database.py::GalleryImage` is the SQLAlchemy model; the rusqlite route
/// module hydrates this struct from a row. Timestamp columns (`taken_at`,
/// `created_at`, `updated_at`) are kept as the raw stored SQLite datetime string
/// (`"%Y-%m-%d %H:%M:%S[.%f]"`), exactly as the other route modules carry them, so
/// `.isoformat()` can be reproduced with [`crate::pydatetime::to_isoformat`].
#[derive(Debug, Clone, Default)]
pub struct GalleryImage {
    pub id: Option<String>,
    pub filename: Option<String>,
    pub prompt: Option<String>,
    pub model: Option<String>,
    pub size: Option<String>,
    pub quality: Option<String>,
    pub tags: Option<String>,
    pub ai_tags: Option<String>,
    pub session_id: Option<String>,
    pub album_id: Option<String>,
    pub is_active: Option<bool>,
    pub favorite: Option<bool>,
    pub taken_at: Option<String>,
    pub camera_make: Option<String>,
    pub camera_model: Option<String>,
    pub gps_lat: Option<String>,
    pub gps_lng: Option<String>,
    pub width: Option<i64>,
    pub height: Option<i64>,
    pub file_size: Option<i64>,
    pub created_at: Option<String>,
    pub updated_at: Option<String>,
}

// ---- EXIF extraction ----

/// `_extract_exif(content: bytes) -> dict` — REAL port (see module doc).
///
/// Decodes `width`/`height` via the `image` crate and the EXIF tag block via
/// `kamadak-exif`, both from the same in-memory bytes, matching the Python key set
/// and value formats. Any decode/parse failure reproduces the Python `except`
/// branch (dimensions stay `None`, `exif_error` recorded) or its inner swallows.
pub fn _extract_exif(content: &[u8]) -> IndexMap<String, Value> {
    use exif::Tag;

    // result = {"width": None, "height": None}
    let mut result: IndexMap<String, Value> = IndexMap::new();
    result.insert("width".to_string(), Value::Null);
    result.insert("height".to_string(), Value::Null);

    // try: img = Image.open(BytesIO(content)); result["width"/"height"] = ...
    //
    // PIL's `Image.open()` is LAZY: `img.width`/`img.height` come from the file
    // HEADER (PNG IHDR / JPEG SOF / TIFF header) WITHOUT decoding the pixel body,
    // and `_extract_exif` never calls `.load()`. So a header-valid but body-corrupt
    // image still yields dimensions in PIL. Mirror that with a HEADER-ONLY read:
    // `ImageReader::with_guessed_format()?.into_dimensions()` constructs the decoder
    // from the header and returns its dimensions without decoding the body — unlike
    // a full `load_from_memory`, which would lose the dims AND set a spurious
    // `exif_error` on a body-corrupt image where PIL succeeds.
    //
    // A failure here (unguessable format / unreadable header) is the Python
    // `except Exception as e` branch.
    // `with_guessed_format()` returns `io::Result` and `into_dimensions()` returns
    // `ImageResult`; a small closure threads the two with `?` (the `io::Error` is
    // promoted into `ImageError` via the crate's `From` impl).
    let dimensions: image::ImageResult<(u32, u32)> = (|| {
        image::ImageReader::new(std::io::Cursor::new(content))
            .with_guessed_format()?
            .into_dimensions()
    })();
    let (raw_width, raw_height) = match dimensions {
        Ok(dims) => dims,
        Err(e) => {
            // `logger.warning(f"EXIF extraction failed: {e}")`
            logger::warning(&format!("EXIF extraction failed: {e}"));
            // `result["exif_error"] = str(e)`
            result.insert("exif_error".to_string(), json!(e.to_string()));
            return result;
        }
    };

    // Python: `img = ImageOps.exif_transpose(img) or img`
    // `exif_transpose` rotates/transposes the image according to the EXIF
    // Orientation tag (and strips that tag). For orientations 5, 6, 7, 8
    // the image is rotated 90° or 270°, which SWAPS the reported width and
    // height. We reproduce this by reading the Orientation tag from the same
    // byte stream and swapping dimensions when needed — without decoding the
    // full pixel body.
    //
    // EXIF Orientation values that swap width/height:
    //   5 = Mirror horizontal, rotate 270° CW
    //   6 = Rotate 90° CW (most common: phone portrait-shot stored landscape)
    //   7 = Mirror horizontal, rotate 90° CW
    //   8 = Rotate 270° CW
    let orientation: u16 = exif::Reader::new()
        .read_from_container(&mut std::io::Cursor::new(content))
        .ok()
        .and_then(|exif| {
            let field = exif.get_field(exif::Tag::Orientation, exif::In::PRIMARY)?;
            match &field.value {
                exif::Value::Short(v) => v.first().copied(),
                _ => None,
            }
        })
        .unwrap_or(1); // default: no rotation

    // Swap width/height for the 90°/270° orientations (5–8).
    let (width, height) = if matches!(orientation, 5..=8) {
        (raw_height, raw_width)
    } else {
        (raw_width, raw_height)
    };

    // result["width"] = img.width; result["height"] = img.height
    result.insert("width".to_string(), json!(width));
    result.insert("height".to_string(), json!(height));

    // exif = img._getexif() if hasattr(img, '_getexif') else None
    // if not exif: return result
    //
    // kamadak-exif reads the EXIF block from the same bytes. An absent or
    // unparseable EXIF block is the Python `if not exif: return result` path
    // (only width/height returned) — kamadak returns Err when there is no EXIF.
    let exif = match exif::Reader::new()
        .read_from_container(&mut std::io::Cursor::new(content))
    {
        Ok(exif) => exif,
        Err(_) => return result,
    };

    // `str(exif.get(271, "")).strip() or None` — Make (271 == 0x10f).
    // `str(exif.get(272, "")).strip() or None` — Model (272 == 0x110).
    // Python always SETS both keys (to the stripped value, or None when empty).
    result.insert(
        "camera_make".to_string(),
        ascii_tag_or_null(&exif, Tag::Make),
    );
    result.insert(
        "camera_model".to_string(),
        ascii_tag_or_null(&exif, Tag::Model),
    );

    // Date taken: first present of DateTimeOriginal (36867 == 0x9003),
    // DateTimeDigitized (36868 == 0x9004), DateTime (306 == 0x132), parsed with
    // strptime("%Y:%m:%d %H:%M:%S"); break on the first that parses.
    for tag in [Tag::DateTimeOriginal, Tag::DateTimeDigitized, Tag::DateTime] {
        if let Some(raw) = ascii_tag(&exif, tag) {
            // `datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S")`.
            if let Ok(dt) = chrono::NaiveDateTime::parse_from_str(
                raw.trim(),
                "%Y:%m:%d %H:%M:%S",
            ) {
                // SQLAlchemy stores a `datetime` into SQLite with 6-digit
                // microseconds; the EXIF value has none, hence `.000000`.
                result.insert(
                    "taken_at".to_string(),
                    json!(dt.format("%Y-%m-%d %H:%M:%S%.6f").to_string()),
                );
                break;
            }
        }
    }

    // GPS: gps_info = exif.get(34853); if dict, read sub-tags 1..=4.
    // The whole block is wrapped in `try: ... except Exception: pass`.
    let lat = gps_to_deg(&exif, Tag::GPSLatitude);
    let lng = gps_to_deg(&exif, Tag::GPSLongitude);
    // `if 2 in gps_info and 4 in gps_info:` — both lat & lng must be present.
    if let (Some(mut lat), Some(mut lng)) = (lat, lng) {
        // `if gps_info.get(1) == 'S': lat = -lat`
        if ascii_tag(&exif, Tag::GPSLatitudeRef).as_deref() == Some("S") {
            lat = -lat;
        }
        // `if gps_info.get(3) == 'W': lng = -lng`
        if ascii_tag(&exif, Tag::GPSLongitudeRef).as_deref() == Some("W") {
            lng = -lng;
        }
        // `result["gps_lat"] = f"{lat:.6f}"`; `result["gps_lng"] = f"{lng:.6f}"`.
        result.insert("gps_lat".to_string(), json!(format!("{lat:.6}")));
        result.insert("gps_lng".to_string(), json!(format!("{lng:.6}")));
    }

    result
}

/// Read an ASCII EXIF tag from the primary IFD as a Rust string.
///
/// PIL's `_getexif()` decodes ASCII tags to `str`. kamadak stores them as
/// `Value::Ascii(Vec<Vec<u8>>)`; we decode the first component as Latin-1 → UTF-8
/// (lossless for the ASCII range PIL also produces) and trim the trailing NUL the
/// EXIF spec terminates ASCII fields with.
fn ascii_tag(exif: &exif::Exif, tag: exif::Tag) -> Option<String> {
    let field = exif.get_field(tag, exif::In::PRIMARY)?;
    match &field.value {
        exif::Value::Ascii(parts) => {
            let bytes = parts.first()?;
            // Latin-1 decode (each byte -> the same Unicode codepoint), then drop a
            // trailing NUL if present (EXIF ASCII fields are NUL-terminated).
            let s: String = bytes
                .iter()
                .take_while(|&&b| b != 0)
                .map(|&b| b as char)
                .collect();
            Some(s)
        }
        _ => None,
    }
}

/// `str(exif.get(id, "")).strip() or None` for an ASCII tag.
fn ascii_tag_or_null(exif: &exif::Exif, tag: exif::Tag) -> Value {
    match ascii_tag(exif, tag) {
        Some(s) if !s.trim().is_empty() => json!(s.trim()),
        _ => Value::Null,
    }
}

/// `_to_deg(vals) = d + m/60 + s/3600` for a GPS lat/long DMS tag (3 rationals).
///
/// kamadak stores GPS coordinates as `Value::Rational([d, m, s])`. Returns `None`
/// when the tag is absent or not the expected 3-rational shape. The Python unpacks
/// `d, m, s = [float(v) for v in vals]`, which raises (caught by the surrounding
/// `except Exception: pass`, leaving GPS unset) unless there are EXACTLY 3 values,
/// so the length check is `== 3`, not `>= 3`.
fn gps_to_deg(exif: &exif::Exif, tag: exif::Tag) -> Option<f64> {
    let field = exif.get_field(tag, exif::In::PRIMARY)?;
    match &field.value {
        exif::Value::Rational(parts) if parts.len() == 3 => {
            // Python computes degrees via `float(v)` on a PIL IFDRational. VERIFIED
            // against the project venv (Pillow 12.2.0): `float(IFDRational(n, 0))`
            // RAISES `ZeroDivisionError`, which the surrounding `except` swallows,
            // leaving GPS entirely UNSET (no `gps_lat`/`gps_lng` keys). So a
            // zero-denominator in ANY component aborts the whole GPS extraction
            // (return `None`) — it must NOT degrade to `0.0`.
            let d = rational_to_f64(parts[0])?;
            let m = rational_to_f64(parts[1])?;
            let s = rational_to_f64(parts[2])?;
            Some(d + m / 60.0 + s / 3600.0)
        }
        _ => None,
    }
}

/// `float(v)` on a PIL IFDRational. PIL's `IFDRational(n, 0)` raises
/// `ZeroDivisionError` (Pillow 12.2.0, verified), which Python's GPS block catches
/// and treats as "no GPS" — so a zero denominator returns `None` (propagated by
/// [`gps_to_deg`] to skip GPS), NOT `0.0` and NOT kamadak's inf/NaN.
fn rational_to_f64(r: exif::Rational) -> Option<f64> {
    if r.denom == 0 {
        None
    } else {
        Some(r.to_f64())
    }
}

// ---- Helpers ----

/// `_image_to_dict(img, session_name=None) -> Dict[str, Any]`.
///
/// Builds the response dict in the EXACT key order of the Python literal
/// (`serde_json` is built with `preserve_order`). Python truthiness/None nuances
/// are reproduced field-by-field (see inline notes).
pub fn _image_to_dict(img: &GalleryImage, session_name: Option<&str>) -> Value {
    // `f"/api/generated-image/{img.filename}"`.
    let url = format!(
        "/api/generated-image/{}",
        img.filename.as_deref().unwrap_or("")
    );

    // `img.tags or ""` — None/"" -> "".
    let tags = or_empty(img.tags.as_deref());
    // `img.ai_tags or ""`.
    let ai_tags = or_empty(img.ai_tags.as_deref());
    // `user_tags` is `img.tags or ""` again (NOT ai_tags).
    let user_tags = tags.clone();

    // `f"{img.camera_make or ''} {img.camera_model or ''}".strip() or None`.
    let camera_make = img.camera_make.as_deref().unwrap_or("");
    let camera_model = img.camera_model.as_deref().unwrap_or("");
    let camera_joined = format!("{camera_make} {camera_model}");
    // Python str.strip() removes leading/trailing ASCII+unicode whitespace; the
    // joined value can only have an interior/edge space, so trim() matches.
    let camera_stripped = camera_joined.trim();
    let camera: Value = if camera_stripped.is_empty() {
        Value::Null
    } else {
        json!(camera_stripped)
    };

    // `{"lat": img.gps_lat, "lng": img.gps_lng} if img.gps_lat else None`.
    // Python truthiness on a string: None/"" -> falsy.
    let gps: Value = match img.gps_lat.as_deref() {
        Some(lat) if !lat.is_empty() => json!({ "lat": lat, "lng": img.gps_lng }),
        _ => Value::Null,
    };

    json!({
        "id": img.id,
        "filename": img.filename,
        "url": url,
        "prompt": img.prompt,
        "model": img.model,
        "size": img.size,
        "quality": img.quality,
        "tags": tags,
        "ai_tags": ai_tags,
        "user_tags": user_tags,
        "session_id": img.session_id,
        "session_name": session_name,
        "album_id": img.album_id,
        "is_active": img.is_active,
        // `img.favorite or False` — None/False -> False.
        "favorite": img.favorite.unwrap_or(false),
        // `img.taken_at.isoformat() if img.taken_at else None`.
        "taken_at": iso_or_null(img.taken_at.as_deref()),
        "camera": camera,
        "gps": gps,
        "width": img.width,
        "height": img.height,
        "file_size": img.file_size,
        // `img.created_at.isoformat() if img.created_at else None`.
        "created_at": iso_or_null(img.created_at.as_deref()),
        "updated_at": iso_or_null(img.updated_at.as_deref()),
    })
}

/// `x or ""` for an optional string — None/"" -> "".
fn or_empty(s: Option<&str>) -> String {
    match s {
        Some(v) if !v.is_empty() => v.to_string(),
        _ => String::new(),
    }
}

/// `dt.isoformat() if dt else None` — plain isoformat (T separator, no `Z`), or null.
fn iso_or_null(stored: Option<&str>) -> Value {
    match stored.filter(|s| !s.is_empty()) {
        Some(s) => json!(crate::pydatetime::to_isoformat(s)),
        None => Value::Null,
    }
}

/// The WHERE-fragment produced by `_owner_filter(q, user)`.
///
/// The Python mutates a SQLAlchemy query (`q.filter(...)`); the rusqlite route
/// module instead assembles SQL text + numbered binds, so the fragment is returned
/// as a string the caller appends to its `WHERE` list. The three cases mirror the
/// Python exactly:
/// * `user is None` -> `return q` (no filter at all) -> the always-true predicate
///   `"1"` (no bind), so every row matches.  This is the no-auth / single-user
///   deployment path: sidebars, tag-cleanup endpoints, etc. all see the full
///   library instead of returning empty results.
/// * `Some(user)` -> `q.filter(GalleryImage.owner == user)` -> `"owner = ?N"` with
///   `user` pushed as the matching bound parameter.
///
/// The old `OwnerFilter::None` variant (SQL `"0"`, match-nothing) is replaced by
/// [`OwnerFilter::All`] (SQL `"1"`, match-everything) for the `user is None` case.
pub enum OwnerFilter {
    /// `return q` — no owner filter, match everything (SQL `"1"`).
    ///
    /// Used when auth is disabled and `get_current_user` returns `None`; the
    /// Python docstring explicitly says the helper "must" treat `None` as
    /// "show everything" so sidebars are not empty in self-hosted deployments.
    All,
    /// `q.filter(GalleryImage.owner == user)` — bind `owner` to this value.
    Owner(String),
}

/// `_owner_filter(q, user)` — apply owner filtering to a gallery query.
///
/// Returns the predicate in a form the raw-SQL route module can splice in. The
/// route module turns [`OwnerFilter::All`] into the literal `"1"` and
/// [`OwnerFilter::Owner`] into `format!("owner = ?{n}")` + a pushed bind, mirroring
/// the `wheres`/`params` assembly used elsewhere in the port.
pub fn _owner_filter(user: Option<&str>) -> OwnerFilter {
    match user {
        // if user is None: return q  (no filter — match everything)
        None => OwnerFilter::All,
        // return q.filter(GalleryImage.owner == user)
        Some(u) => OwnerFilter::Owner(u.to_string()),
    }
}

/// `_human_size(nbytes)` — human-readable byte size.
///
/// ```text
/// for unit in ['B','KB','MB','GB','TB']:
///     if abs(nbytes) < 1024: return f"{nbytes:.1f} {unit}"
///     nbytes /= 1024
/// return f"{nbytes:.1f} PB"
/// ```
/// Python's `nbytes /= 1024` promotes the int to float on the first divide, and
/// `f"{x:.1f}"` formats with one decimal (round-half-to-even). `nbytes` arrives as
/// an integer byte count (`file_size`), so we take `i64` and carry it as `f64`,
/// matching Python's float arithmetic and `:.1f` rounding.
pub fn _human_size(nbytes: i64) -> String {
    let mut n = nbytes as f64;
    for unit in ["B", "KB", "MB", "GB", "TB"] {
        // `if abs(nbytes) < 1024:`
        if n.abs() < 1024.0 {
            return format!("{n:.1} {unit}");
        }
        n /= 1024.0;
    }
    format!("{n:.1} PB")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extract_exif_decode_error_branch() {
        // Not a decodable image -> Python `except Exception as e` branch:
        // width/height stay None and `exif_error` is set (to the decode error).
        let r = _extract_exif(b"\xff\xd8\xff\xe0not a real jpeg");
        let keys: Vec<&String> = r.keys().collect();
        assert_eq!(keys, vec!["width", "height", "exif_error"]);
        assert_eq!(r["width"], Value::Null);
        assert_eq!(r["height"], Value::Null);
        assert!(r["exif_error"].is_string());
        // No camera/gps/taken_at keys on the decode-failure path.
        assert!(!r.contains_key("taken_at"));
        assert!(!r.contains_key("camera_make"));
        assert!(!r.contains_key("gps_lat"));
    }

    #[test]
    fn extract_exif_dimensions_no_exif() {
        // A real (tiny) PNG with no EXIF block -> Python `if not exif: return
        // result` path: only width/height are set, no camera/gps/taken_at keys.
        let img = image::RgbImage::new(7, 3);
        let mut buf = std::io::Cursor::new(Vec::new());
        image::DynamicImage::ImageRgb8(img)
            .write_to(&mut buf, image::ImageFormat::Png)
            .unwrap();
        let r = _extract_exif(buf.get_ref());
        let keys: Vec<&String> = r.keys().collect();
        assert_eq!(keys, vec!["width", "height"]);
        assert_eq!(r["width"], json!(7u32));
        assert_eq!(r["height"], json!(3u32));
        assert!(!r.contains_key("exif_error"));
        assert!(!r.contains_key("camera_make"));
        assert!(!r.contains_key("taken_at"));
        assert!(!r.contains_key("gps_lat"));
    }

    #[test]
    fn extract_exif_header_dims_without_body_decode() {
        // PIL's lazy `Image.open()` reads width/height from the PNG IHDR without
        // decoding the pixel body. Build a valid PNG, then corrupt the compressed
        // image body (IDAT) so a FULL decode would fail — the HEADER-ONLY read must
        // still recover the dimensions and NOT set `exif_error`, matching PIL.
        let img = image::RgbImage::new(11, 5);
        let mut buf = std::io::Cursor::new(Vec::new());
        image::DynamicImage::ImageRgb8(img)
            .write_to(&mut buf, image::ImageFormat::Png)
            .unwrap();
        let mut bytes = buf.into_inner();
        // Flip bytes in the back half (the IDAT zlib stream lives after IHDR) to
        // break the compressed body while leaving the 8-byte signature + IHDR (with
        // its width/height) intact at the front.
        let start = bytes.len() * 2 / 3;
        for b in &mut bytes[start..] {
            *b ^= 0xFF;
        }
        // Sanity: a full decode now fails on the corrupted body.
        assert!(image::load_from_memory(&bytes).is_err());

        let r = _extract_exif(&bytes);
        // Header dims still recovered; no spurious `exif_error` (PIL parity).
        assert_eq!(r["width"], json!(11u32));
        assert_eq!(r["height"], json!(5u32));
        assert!(!r.contains_key("exif_error"));
    }

    /// Build a minimal valid JPEG with a hand-crafted EXIF APP1 segment that sets
    /// Orientation to the given value (1–8).  The JPEG payload is a tiny 1×1 white
    /// image so `into_dimensions()` can read the SOF header.
    ///
    /// JPEG structure used:
    ///   SOI | APP1(EXIF with Orientation) | SOF0 (dims) | SOS (header only) | EOI
    fn jpeg_with_orientation(width: u16, height: u16, orientation: u16) -> Vec<u8> {
        // --- EXIF APP1 ---
        // TIFF header (little-endian): II + 0x002A + offset-to-IFD (= 8)
        // IFD0: 1 entry (Orientation tag 0x0112, type SHORT=3, count=1, value=orientation)
        // next-IFD offset = 0 (end)
        let mut exif_body: Vec<u8> = Vec::new();
        // TIFF LE magic
        exif_body.extend_from_slice(b"II");        // byte order: little-endian
        exif_body.extend_from_slice(&0x002Au16.to_le_bytes()); // magic 42
        exif_body.extend_from_slice(&8u32.to_le_bytes());      // offset to IFD0 (from TIFF start)
        // IFD0: 1 entry
        exif_body.extend_from_slice(&1u16.to_le_bytes());      // entry count
        // Entry: tag=0x0112 (274), type=3 (SHORT), count=1, value (LE in 4-byte value field)
        exif_body.extend_from_slice(&0x0112u16.to_le_bytes()); // tag
        exif_body.extend_from_slice(&3u16.to_le_bytes());      // type SHORT
        exif_body.extend_from_slice(&1u32.to_le_bytes());      // count
        exif_body.extend_from_slice(&(orientation as u32).to_le_bytes()); // value
        // next IFD offset = 0
        exif_body.extend_from_slice(&0u32.to_le_bytes());

        // APP1 marker: 0xFFE1, length = 2 + 6 + exif_body.len()
        // "Exif\0\0" prefix (6 bytes)
        let app1_data_len = 6 + exif_body.len();
        let app1_length = (app1_data_len + 2) as u16; // includes the 2 length bytes

        let mut jpeg: Vec<u8> = Vec::new();
        // SOI
        jpeg.extend_from_slice(&[0xFF, 0xD8]);
        // APP1
        jpeg.extend_from_slice(&[0xFF, 0xE1]);
        jpeg.extend_from_slice(&app1_length.to_be_bytes());
        jpeg.extend_from_slice(b"Exif\x00\x00");
        jpeg.extend_from_slice(&exif_body);
        // SOF0: marker, length=17, precision=8, height, width, components=3 (YCbCr)
        jpeg.extend_from_slice(&[0xFF, 0xC0]);
        jpeg.extend_from_slice(&17u16.to_be_bytes()); // length (includes 2 length bytes)
        jpeg.push(8u8);                               // precision
        jpeg.extend_from_slice(&height.to_be_bytes());
        jpeg.extend_from_slice(&width.to_be_bytes());
        jpeg.push(3u8);                               // number of components
        // 3 component descriptors (id, sampling, qtable)
        for id in 1u8..=3 {
            jpeg.push(id);
            jpeg.push(0x11); // sampling 1x1
            jpeg.push(0);    // qtable 0
        }
        // SOS (Start of Scan) — zune-jpeg's decode_headers() loops until it sees SOS;
        // without SOS it hits EOI and returns "Premature End of image", causing
        // into_dimensions() to fail and _extract_exif to return width/height as Null.
        //
        // SOS layout for a 3-component baseline scan:
        //   marker  : 0xFF 0xDA
        //   length  : 12 (= 6 + 2×Ns; includes the 2 length bytes)
        //   Ns      : 3 (number of components in this scan)
        //   Cs1=1, Td1/Ta1=0x00   (component 1, DC table 0, AC table 0)
        //   Cs2=2, Td2/Ta2=0x00   (component 2)
        //   Cs3=3, Td3/Ta3=0x00   (component 3)
        //   Ss=0, Se=63, Ah/Al=0  (spectral selection / successive approx.)
        //
        // zune-jpeg returns Ok after parse_sos succeeds, then decode_headers_internal
        // sees Marker::SOS, sets headers_decoded=true, and returns — no scan data needed.
        jpeg.extend_from_slice(&[0xFF, 0xDA]); // SOS marker
        jpeg.extend_from_slice(&12u16.to_be_bytes()); // length = 12
        jpeg.push(3u8);                               // Ns = 3 components
        jpeg.push(1u8); jpeg.push(0x00u8);            // Cs=1, Td/Ta=0
        jpeg.push(2u8); jpeg.push(0x00u8);            // Cs=2, Td/Ta=0
        jpeg.push(3u8); jpeg.push(0x00u8);            // Cs=3, Td/Ta=0
        jpeg.push(0u8);                               // Ss = 0
        jpeg.push(0x3Fu8);                            // Se = 63
        jpeg.push(0x00u8);                            // Ah=0, Al=0
        // EOI
        jpeg.extend_from_slice(&[0xFF, 0xD9]);
        jpeg
    }

    #[test]
    fn extract_exif_orientation_6_swaps_dims() {
        // Phone portrait shot stored landscape: raw 640×480, Orientation=6 (90° CW).
        // After exif_transpose the display size is 480×640 (width/height swapped).
        // The Rust port must store the DISPLAY dimensions, matching Python's
        // `img = ImageOps.exif_transpose(img); result["width"] = img.width`.
        let bytes = jpeg_with_orientation(640, 480, 6);
        let r = _extract_exif(&bytes);
        // Display width = raw height, display height = raw width.
        assert_eq!(r["width"], json!(480u32), "orientation 6 must swap: display width = raw height");
        assert_eq!(r["height"], json!(640u32), "orientation 6 must swap: display height = raw width");
    }

    #[test]
    fn extract_exif_orientation_8_swaps_dims() {
        // Orientation=8 (270° CW) also swaps width/height.
        let bytes = jpeg_with_orientation(320, 240, 8);
        let r = _extract_exif(&bytes);
        assert_eq!(r["width"], json!(240u32));
        assert_eq!(r["height"], json!(320u32));
    }

    #[test]
    fn extract_exif_orientation_1_keeps_dims() {
        // Orientation=1 (normal / no rotation): dimensions unchanged.
        let bytes = jpeg_with_orientation(320, 240, 1);
        let r = _extract_exif(&bytes);
        assert_eq!(r["width"], json!(320u32));
        assert_eq!(r["height"], json!(240u32));
    }

    #[test]
    fn extract_exif_orientation_3_keeps_dims() {
        // Orientation=3 (180° rotation): no swap — only the pixel content flips,
        // not the axis assignment.
        let bytes = jpeg_with_orientation(320, 240, 3);
        let r = _extract_exif(&bytes);
        assert_eq!(r["width"], json!(320u32));
        assert_eq!(r["height"], json!(240u32));
    }

    #[test]
    fn rational_zero_denominator_matches_pil() {
        // PIL (Pillow 12.2.0, venv-verified): `float(IFDRational(n, 0))` raises
        // ZeroDivisionError, swallowed by the GPS `except` -> GPS unset. We map a
        // zero denominator to `None` so `gps_to_deg` skips GPS entirely.
        let zero_denom = exif::Rational { num: 42, denom: 0 };
        assert_eq!(rational_to_f64(zero_denom), None);
        // A normal rational is unaffected.
        let normal = exif::Rational { num: 3, denom: 2 };
        assert_eq!(rational_to_f64(normal), Some(1.5));
    }

    #[test]
    fn human_size_matches_python_format() {
        // f"{0:.1f} B"
        assert_eq!(_human_size(0), "0.0 B");
        assert_eq!(_human_size(512), "512.0 B");
        // 1023 < 1024 -> still bytes.
        assert_eq!(_human_size(1023), "1023.0 B");
        // 1024 -> 1.0 KB
        assert_eq!(_human_size(1024), "1.0 KB");
        // 1536 -> 1.5 KB
        assert_eq!(_human_size(1536), "1.5 KB");
        // 1048576 -> 1.0 MB
        assert_eq!(_human_size(1_048_576), "1.0 MB");
        // 1073741824 -> 1.0 GB
        assert_eq!(_human_size(1_073_741_824), "1.0 GB");
        // 1099511627776 -> 1.0 TB
        assert_eq!(_human_size(1_099_511_627_776), "1.0 TB");
        // 1125899906842624 -> 1.0 PB (falls through the loop).
        assert_eq!(_human_size(1_125_899_906_842_624), "1.0 PB");
    }

    #[test]
    fn owner_filter_none_vs_user() {
        // user=None -> All (match everything, SQL "1") — no-auth single-user mode
        // must show the full library, not an empty one (parity change).
        match _owner_filter(None) {
            OwnerFilter::All => {}
            _ => panic!("None user must produce All (match-everything)"),
        }
        match _owner_filter(Some("alice")) {
            OwnerFilter::Owner(u) => assert_eq!(u, "alice"),
            _ => panic!("Some user must bind owner"),
        }
    }

    #[test]
    fn image_to_dict_key_order_and_truthiness() {
        let img = GalleryImage {
            id: Some("img1".to_string()),
            filename: Some("abc.png".to_string()),
            prompt: Some("a cat".to_string()),
            model: Some("imported".to_string()),
            tags: Some("a,b".to_string()),
            ai_tags: None, // -> ""
            favorite: None, // -> false
            gps_lat: None, // -> gps null
            camera_make: None,
            camera_model: None,
            ..Default::default()
        };
        let v = _image_to_dict(&img, Some("sess"));
        let obj = v.as_object().unwrap();
        let keys: Vec<&str> = obj.keys().map(|k| k.as_str()).collect();
        assert_eq!(
            keys,
            vec![
                "id", "filename", "url", "prompt", "model", "size", "quality",
                "tags", "ai_tags", "user_tags", "session_id", "session_name",
                "album_id", "is_active", "favorite", "taken_at", "camera", "gps",
                "width", "height", "file_size", "created_at", "updated_at",
            ]
        );
        assert_eq!(v["url"], json!("/api/generated-image/abc.png"));
        assert_eq!(v["tags"], json!("a,b"));
        assert_eq!(v["ai_tags"], json!(""));
        assert_eq!(v["user_tags"], json!("a,b"));
        assert_eq!(v["session_name"], json!("sess"));
        assert_eq!(v["favorite"], json!(false));
        assert_eq!(v["camera"], Value::Null);
        assert_eq!(v["gps"], Value::Null);
        assert_eq!(v["taken_at"], Value::Null);
    }

    #[test]
    fn image_to_dict_camera_and_gps_present() {
        let img = GalleryImage {
            camera_make: Some("Canon".to_string()),
            camera_model: Some("EOS".to_string()),
            gps_lat: Some("12.345678".to_string()),
            gps_lng: Some("-9.876543".to_string()),
            taken_at: Some("2024-01-02 03:04:05".to_string()),
            favorite: Some(true),
            ..Default::default()
        };
        let v = _image_to_dict(&img, None);
        // `f"{make} {model}".strip()`.
        assert_eq!(v["camera"], json!("Canon EOS"));
        assert_eq!(v["gps"], json!({ "lat": "12.345678", "lng": "-9.876543" }));
        // isoformat, no fraction -> no microseconds.
        assert_eq!(v["taken_at"], json!("2024-01-02T03:04:05"));
        assert_eq!(v["favorite"], json!(true));
        assert_eq!(v["session_name"], Value::Null);
    }

    #[test]
    fn image_to_dict_camera_make_only() {
        // `f"{make or ''} {'' }".strip()` -> "Canon" (trailing space trimmed).
        let img = GalleryImage {
            camera_make: Some("Canon".to_string()),
            camera_model: None,
            ..Default::default()
        };
        let v = _image_to_dict(&img, None);
        assert_eq!(v["camera"], json!("Canon"));
    }
}
