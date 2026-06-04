// src/pdf_render.rs  <- the PyMuPDF(`fitz`) render / page-geometry /
//                       positioned-text / page-burn slices that `lopdf` cannot
//                       cover, re-backed onto `pdfium-render`.
//! Shared PDFium-backed PDF operations for the PDF cluster.
//!
//! ## What this backs (and the Python it replaces)
//!
//! The Python uses PyMuPDF (`fitz`, AGPL-3.0) for four things `lopdf`
//! (the AcroForm OBJECT engine in `pdf_forms.rs`) cannot do:
//!
//!   * **A — render-page-png** (`routes/document_routes.py:1055-1062`):
//!     `fitz.open -> page.get_pixmap(matrix=Matrix(scale,scale), alpha=False)
//!     -> pix.tobytes("png")`. Ported as [`render_page_png`].
//!   * **B — render-pages geometry** (`routes/document_routes.py:994-1023`):
//!     per-page `page.rect` width/height for the interactive viewer. Ported as
//!     [`page_geometries`] (field rects come from the on-disk sidecar, NOT from
//!     pdfium — see [`PageGeom`]).
//!   * **D — stamp_annotations BURN** (`src/pdf_forms.py:259-365`): draw the
//!     filled values onto the page (text via `insert_text`, checkmark via
//!     `new_shape().draw_polyline`, signature via `insert_image`). Ported as
//!     [`stamp_plans`] (the real backing for `pdf_forms::stamp_annotations`).
//!   * **E — positioned-text label inference** (`src/pdf_forms.py:91-128`):
//!     `page.get_text("words")` (words + bounding boxes) so `_infer_label` can
//!     pick the closest word to a widget. Ported as [`page_words`].
//!
//! ## PDFium provisioning (the native dependency)
//!
//! `pdfium-render` does NOT bundle `libpdfium`; it binds a dynamic library at
//! runtime. We download the prebuilt binary from the bblanchon
//! `pdfium-binaries` GitHub releases for the host platform, extract the single
//! dylib member from the `.tgz` (gzip via `flate2`, tar via `tar`), cache it
//! under `DATA_DIR/pdfium` (atomic `.part` rename — exactly like
//! `image_models::ensure_model`), and bind it ONCE behind a process-global
//! `OnceCell`. On no-network / unsupported-platform the provisioning `Err` is a
//! friendly `String`; the HTTP handlers turn it into the SAME honest 500 the
//! FastAPI app emits when `import fitz` fails (`ModuleNotFoundError`), and the
//! library-level callers (`stamp_annotations`, `_infer_label`) log + return
//! `0` / fall back. NEVER a faked success.
//!
//! ## Coordinate spaces (the one critical agreement)
//!
//! pdfium `PdfRect`/`PdfPoints` are PDF-native BOTTOM-LEFT (y up). `fitz`
//! `page.rect`, the field sidecar rects, and the editor's page-percentage
//! annotations are all TOP-LEFT (y down). RULE: these fns return/accept
//! TOP-LEFT coords at their public boundary. [`page_words`] flips y
//! (`y_tl = page_h - pdfium_top`); [`page_geometries`] returns plain w/h (no
//! flip); [`stamp_plans`] receives plans whose y is already TOP-LEFT (computed
//! by `plan_annotation`) and flips internally at draw time
//! (`pdfium_y = page_height - top_y`).
//!
//! ## Concurrency
//!
//! All pdfium handles are `!Send` across `.await`, and the work is CPU/FFI
//! bound, so every CALLER wraps these (synchronous) fns in
//! `tokio::task::spawn_blocking` (the same pattern the `image_models` ONNX
//! handlers use). The `reqwest::blocking` download inside provisioning runs in
//! that same blocking scope. `thread_safe` Mutex-wraps the process-global
//! bindings so touching the global `Pdfium` from a tokio worker is safe.

use crate::core::constants::DATA_DIR;
use crate::pylog as logger;
use crate::pyos as os;
use once_cell::sync::OnceCell;
use pdfium_render::prelude::*;
use std::collections::HashMap;
use std::path::PathBuf;

// ---------------------------------------------------------------------------
// PDFium provisioning (download bblanchon .tgz -> extract dylib -> bind once)
// ---------------------------------------------------------------------------

/// The friendly message surfaced when PDFium cannot be provisioned (offline
/// first run, unsupported platform, corrupt archive). HTTP handlers map this to
/// the SAME 500 the Python emits on `import fitz` failure; library callers log
/// it and degrade. Carries the underlying cause for the logs.
fn unavailable(cause: impl std::fmt::Display) -> String {
    format!("PDF rendering unavailable (PDFium could not be provisioned): {cause}")
}

/// The bblanchon `pdfium-binaries` release asset for the host platform, plus the
/// file name of the dynamic-library member inside the `.tgz`.
///
/// Compile-time `cfg` match on the host triple. An unmatched platform yields
/// `Err` -> the honest 500. (`member` is matched on the FILE NAME only so it is
/// robust to a `lib/` vs `bin/` layout drift in the archive.)
fn platform_asset() -> Result<(&'static str, &'static str), String> {
    // (asset .tgz name, dylib member file name)
    #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
    {
        return Ok(("pdfium-mac-arm64.tgz", "libpdfium.dylib"));
    }
    #[cfg(all(target_os = "macos", target_arch = "x86_64"))]
    {
        return Ok(("pdfium-mac-x64.tgz", "libpdfium.dylib"));
    }
    #[cfg(all(target_os = "linux", target_arch = "x86_64"))]
    {
        return Ok(("pdfium-linux-x64.tgz", "libpdfium.so"));
    }
    #[cfg(all(target_os = "linux", target_arch = "aarch64"))]
    {
        return Ok(("pdfium-linux-arm64.tgz", "libpdfium.so"));
    }
    #[cfg(all(target_os = "windows", target_arch = "x86_64"))]
    {
        return Ok(("pdfium-win-x64.tgz", "pdfium.dll"));
    }
    #[allow(unreachable_code)]
    {
        Err("no prebuilt PDFium binary for this platform".to_string())
    }
}

/// `DATA_DIR/pdfium` — the cache root (mirrors `image_models`' `DATA_DIR/onnx_models`).
fn cache_dir() -> String {
    os::path::join(&DATA_DIR, "pdfium")
}

/// Ensure the prebuilt `libpdfium` is downloaded + extracted to
/// `DATA_DIR/pdfium/<platform_lib_name>`; return its path.
///
/// Cached (non-empty file present) -> returns immediately, no network.
/// Otherwise downloads the platform `.tgz` from the bblanchon `latest` release,
/// gunzips (`flate2`) + untars (`tar`) the single dylib member to a scratch
/// `.part` then atomically renames it into place. Any network/IO/extract error
/// returns `Err(<friendly message>)` — never a panic.
fn ensure_pdfium_library() -> Result<PathBuf, String> {
    let (asset, member) = platform_asset().map_err(unavailable)?;

    let dir = cache_dir();
    os::makedirs(&dir, true).map_err(|e| unavailable(format!("pdfium cache dir: {e}")))?;

    // The extracted library lands under the platform library name pdfium-render
    // expects (libpdfium.dylib / libpdfium.so / pdfium.dll), so a later
    // `bind_to_library` finds it deterministically.
    let lib_name = Pdfium::pdfium_platform_library_name();
    let lib_path = PathBuf::from(&dir).join(&lib_name);

    if let Ok(meta) = std::fs::metadata(&lib_path) {
        if meta.is_file() && meta.len() > 0 {
            return Ok(lib_path);
        }
    }

    let url = format!(
        "https://github.com/bblanchon/pdfium-binaries/releases/latest/download/{asset}"
    );

    // reqwest::blocking is already a `web` dep (used by image_models too). A
    // generous timeout covers the ~5-6 MB download + redirect to GitHub's
    // release-assets CDN.
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(600))
        .build()
        .map_err(|e| unavailable(format!("http client: {e}")))?;

    let resp = client
        .get(&url)
        .send()
        .map_err(|e| unavailable(format!("GET {url}: {e}")))?;
    if !resp.status().is_success() {
        return Err(unavailable(format!(
            "GET {url}: HTTP {}",
            resp.status().as_u16()
        )));
    }
    let bytes = resp
        .bytes()
        .map_err(|e| unavailable(format!("read {url}: {e}")))?;

    // Decode the gzip layer then iterate the tar entries; extract the single
    // dylib member (matched by file name) to `<lib_path>.part`, then rename.
    let part = part_path(&lib_path);
    let mut archive = tar::Archive::new(flate2::read::GzDecoder::new(&bytes[..]));
    let entries = archive
        .entries()
        .map_err(|e| unavailable(format!("tar open: {e}")))?;

    let mut extracted = false;
    for entry in entries {
        let mut entry = match entry {
            Ok(e) => e,
            Err(e) => return Err(unavailable(format!("tar entry: {e}"))),
        };
        let entry_path = match entry.path() {
            Ok(p) => p.into_owned(),
            Err(_) => continue,
        };
        let file_name = entry_path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("");
        if file_name != member {
            continue;
        }
        let mut out =
            std::fs::File::create(&part).map_err(|e| unavailable(format!("create part: {e}")))?;
        std::io::copy(&mut entry, &mut out)
            .map_err(|e| unavailable(format!("extract {member}: {e}")))?;
        extracted = true;
        break;
    }

    if !extracted {
        let _ = std::fs::remove_file(&part);
        return Err(unavailable(format!(
            "libpdfium not found in archive {asset}"
        )));
    }

    // os.replace(part, lib_path) — atomic on the same filesystem.
    std::fs::rename(&part, &lib_path).map_err(|e| {
        let _ = std::fs::remove_file(&part);
        unavailable(format!(
            "rename {} -> {}: {e}",
            part.display(),
            lib_path.display()
        ))
    })?;

    Ok(lib_path)
}

/// `<lib_path>.part` — the atomic-download/extract scratch path.
fn part_path(target: &std::path::Path) -> PathBuf {
    let mut p = target.to_path_buf();
    let name = format!(
        "{}.part",
        target
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("libpdfium")
    );
    p.set_file_name(name);
    p
}

/// Memoizes the bind outcome so an offline first call caches the friendly `Err`
/// and later calls return the SAME error WITHOUT re-hammering the network every
/// request. A later successful provision still self-heals across process
/// restarts because the cache file under `DATA_DIR/pdfium` persists.
static PDFIUM_READY: OnceCell<Result<(), String>> = OnceCell::new();

/// Ensure the prebuilt `libpdfium` is downloaded + extracted and `pdfium-render`
/// is bound to it (process-global, once). `Ok` = ready; `Err` = friendly
/// message (offline first run / unsupported platform / corrupt archive).
///
/// Binding seeds `pdfium-render`'s OWN process-global `BINDINGS` `OnceCell`:
/// `Pdfium::bind_to_library` returns the dynamic bindings, then `Pdfium::new`
/// calls `FPDF_InitLibrary` and stashes them — afterwards every cheap
/// `Pdfium::default()` re-uses that global. We immediately drop the seeded
/// handle; the bindings live in the crate's global, not in a static of ours.
/// A concurrent-race `PdfiumLibraryBindingsAlreadyInitialized` is treated as Ok
/// (idempotent).
pub fn ensure_pdfium() -> Result<(), String> {
    PDFIUM_READY
        .get_or_init(|| {
            let lib = ensure_pdfium_library()?;
            match Pdfium::bind_to_library(&lib) {
                Ok(bindings) => {
                    // Seed the crate's global BINDINGS (FPDF_InitLibrary). The
                    // handle is dropped immediately; later Pdfium::default()
                    // re-uses the now-set global.
                    let _seed = Pdfium::new(bindings);
                    drop(_seed);
                    Ok(())
                }
                // Another thread already bound it (OnceCell race) — fine.
                Err(PdfiumError::PdfiumLibraryBindingsAlreadyInitialized) => Ok(()),
                Err(e) => Err(unavailable(format!("bind_to_library: {e:?}"))),
            }
        })
        .clone()
}

/// Construct a usable `Pdfium` over the already-bound global bindings (calls
/// [`ensure_pdfium`] first). `Pdfium::default()` re-uses the seeded global; it
/// is safe ONLY because `ensure_pdfium` guarantees `BINDINGS` is set before we
/// reach here (otherwise `default()` would attempt a CWD/system fallback and
/// could panic).
fn pdfium() -> Result<Pdfium, String> {
    ensure_pdfium()?;
    Ok(Pdfium::default())
}

// ---------------------------------------------------------------------------
// A — render-page-png  (document_routes render_page_png)
// ---------------------------------------------------------------------------

/// The outcome of [`render_page_png`].
///
/// * `Png(bytes)` — the rendered PNG (HTTP 200, `image/png`).
/// * `OutOfRange` — `page_no < 1` or `> page_count` (HTTP 404, "Page out of range").
/// * `Unavailable(msg)` — PDFium could not be provisioned, OR the PDF could not
///   be opened/rendered (HTTP 500 — the honest `import fitz` parity).
#[derive(Debug)]
pub enum RenderOutcome {
    Png(Vec<u8>),
    OutOfRange,
    Unavailable(String),
}

/// Open `pdf_path`, rasterize the 1-based `page_no` at `scale` (the fitz
/// `Matrix(scale, scale)`, `alpha=false`), and PNG-encode the bitmap.
///
/// `render_form_data(true)` matches fitz's default of rendering the form-field
/// appearances into the pixmap, so a viewer-overlay frontend sees the same base
/// image. Synchronous — the CALLER must wrap this in `spawn_blocking`.
pub fn render_page_png(pdf_path: &str, page_no: i64, scale: f32) -> RenderOutcome {
    let pdfium = match pdfium() {
        Ok(p) => p,
        Err(e) => return RenderOutcome::Unavailable(e),
    };
    let doc = match pdfium.load_pdf_from_file(pdf_path, None) {
        Ok(d) => d,
        Err(e) => {
            return RenderOutcome::Unavailable(unavailable(format!(
                "open {pdf_path}: {e:?}"
            )))
        }
    };
    let pages = doc.pages();
    let page_count = pages.len() as i64;
    if page_no < 1 || page_no > page_count {
        return RenderOutcome::OutOfRange;
    }
    let page = match pages.get((page_no - 1) as PdfPageIndex) {
        Ok(p) => p,
        Err(e) => {
            return RenderOutcome::Unavailable(unavailable(format!(
                "page {page_no}: {e:?}"
            )))
        }
    };

    let config = PdfRenderConfig::new()
        .scale_page_by_factor(scale)
        .render_form_data(true);
    let bitmap = match page.render_with_config(&config) {
        Ok(b) => b,
        Err(e) => {
            return RenderOutcome::Unavailable(unavailable(format!(
                "render page {page_no}: {e:?}"
            )))
        }
    };
    let image = match bitmap.as_image() {
        Ok(img) => img,
        Err(e) => {
            return RenderOutcome::Unavailable(unavailable(format!("as_image: {e:?}")))
        }
    };
    let mut buf: Vec<u8> = Vec::new();
    if let Err(e) = image.write_to(&mut std::io::Cursor::new(&mut buf), image::ImageFormat::Png) {
        return RenderOutcome::Unavailable(unavailable(format!("png encode: {e}")));
    }
    RenderOutcome::Png(buf)
}

// ---------------------------------------------------------------------------
// B — render-pages geometry  (document_routes render_pages)
// ---------------------------------------------------------------------------

/// Per-page pixel geometry for the interactive viewer.
///
/// `width = int(page.rect.width * scale)`, `height = int(page.rect.height *
/// scale)` — TRUNCATION, to byte-match the Python `int(pw*scale)`. Field rects
/// are NOT here: the route reuses the on-disk field sidecar rects (already in
/// fitz top-left space) and scales them, EXACTLY like the Python which reads
/// `f["rect"]` from the schema. pdfium supplies ONLY the page dimensions.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PageGeom {
    pub page: i64,
    pub width: i64,
    pub height: i64,
}

/// Per-page geometry for every page of `pdf_path`, scaled by `scale` (truncated
/// to match `int()`). `Err(msg)` when pdfium is unavailable or the PDF cannot be
/// opened. Synchronous — the CALLER must wrap this in `spawn_blocking`.
pub fn page_geometries(pdf_path: &str, scale: f32) -> Result<Vec<PageGeom>, String> {
    let pdfium = pdfium()?;
    let doc = pdfium
        .load_pdf_from_file(pdf_path, None)
        .map_err(|e| unavailable(format!("open {pdf_path}: {e:?}")))?;
    let scale = scale as f64;
    let mut out = Vec::new();
    for (index, page) in doc.pages().iter().enumerate() {
        let pw = page.width().value as f64;
        let ph = page.height().value as f64;
        out.push(PageGeom {
            page: (index as i64) + 1,
            // TRUNCATION, matching Python int(pw*scale).
            width: (pw * scale) as i64,
            height: (ph * scale) as i64,
        });
    }
    Ok(out)
}

/// Page dimensions in USER UNITS (no scale) as `(width, height)` per page, in
/// fitz top-left orientation (dimensions are orientation-agnostic). Used by
/// `pdf_forms::stamp_annotations` to bound-check + place each annotation's
/// page-percentage geometry (the Python reads `page.rect.width/height`).
pub fn page_dims(pdf_path: &str) -> Result<Vec<(f64, f64)>, String> {
    let pdfium = pdfium()?;
    let doc = pdfium
        .load_pdf_from_file(pdf_path, None)
        .map_err(|e| unavailable(format!("open {pdf_path}: {e:?}")))?;
    Ok(doc
        .pages()
        .iter()
        .map(|page| (page.width().value as f64, page.height().value as f64))
        .collect())
}

// ---------------------------------------------------------------------------
// D — stamp_annotations BURN  (called by pdf_forms::stamp_annotations)
// ---------------------------------------------------------------------------

/// Burn the pre-computed [`AnnotationDrawPlan`] list onto each page of
/// `pdf_path` and save to `output_path`. Returns the number of annotations
/// actually drawn (parity with the Python `written` counter), or `Err(msg)`
/// when pdfium is unavailable (so the caller logs + returns 0 — honest, never a
/// fake count).
///
/// Plans carry user-unit geometry in fitz TOP-LEFT space (as
/// `pdf_forms::plan_annotation` already computes); this fn flips y to pdfium
/// BOTTOM-LEFT at draw time:
///   * text — the plan's `yy` is a baseline measured DOWN from the page top, and
///     each subsequent line advances `yy` DOWN by `line_box`; in pdfium space
///     the baseline is `page_h - yy` and each next line SUBTRACTS `line_box`.
///   * check / signature — `py = page_h - y_topleft`.
///
/// `signature_pngs: {sid: png_bytes}` supplies the raster for `kind==signature`.
pub fn stamp_plans(
    pdf_path: &str,
    output_path: &str,
    plans: &[(i64, crate::src::pdf_forms::AnnotationDrawPlan)],
    signature_pngs: &HashMap<String, Vec<u8>>,
) -> Result<i64, String> {
    use crate::src::pdf_forms::AnnotationDrawPlan as Plan;

    let pdfium = pdfium()?;
    let mut doc = pdfium
        .load_pdf_from_file(pdf_path, None)
        .map_err(|e| unavailable(format!("open {pdf_path}: {e:?}")))?;

    // One Helvetica token for every text line (the fitz default font).
    let helvetica = doc.fonts_mut().helvetica();

    let page_count = doc.pages().len() as i64;
    let mut written: i64 = 0;

    for (page_no, plan) in plans {
        if *page_no < 1 || *page_no > page_count {
            continue;
        }
        let mut page = match doc.pages().get((*page_no - 1) as PdfPageIndex) {
            Ok(p) => p,
            Err(e) => {
                logger::warning(&format!(
                    "stamp_plans: page {page_no} unavailable: {e:?}"
                ));
                continue;
            }
        };
        let page_h = page.height().value as f64;

        match plan {
            Plan::Skip => {}
            Plan::Text {
                xx,
                yy,
                line_box,
                fontsize,
                lines,
            } => {
                // `yy` is a TOP-LEFT baseline (y-down). In pdfium bottom-left
                // the first baseline is `page_h - yy`; each next line moves DOWN
                // in top-left -> DOWN in bottom-left too (subtract line_box).
                let mut baseline_tl = *yy;
                let mut drew_any = false;
                for line in lines {
                    let py = page_h - baseline_tl;
                    match page.objects_mut().create_text_object(
                        PdfPoints::new(*xx as f32),
                        PdfPoints::new(py as f32),
                        line,
                        helvetica,
                        PdfPoints::new(*fontsize as f32),
                    ) {
                        Ok(mut obj) => {
                            // fitz draws color=(0,0,0) — black fill.
                            if let Err(e) = obj.set_fill_color(PdfColor::BLACK) {
                                logger::warning(&format!(
                                    "stamp_plans: set_fill_color: {e:?}"
                                ));
                            }
                            drew_any = true;
                        }
                        Err(e) => {
                            logger::warning(&format!(
                                "stamp_plans: create_text_object: {e:?}"
                            ));
                        }
                    }
                    baseline_tl += *line_box;
                }
                // Python increments `written` once per text annotation (not per
                // line), after attempting all lines — even if an individual
                // insert_text failed. We mirror that: count the annotation when
                // we attempted to draw it (had >= 1 line) regardless of per-line
                // failures, matching `written += 1` outside the per-line loop.
                if !lines.is_empty() {
                    let _ = drew_any;
                    written += 1;
                }
            }
            Plan::Check { points, width } => {
                // 3-point polyline == two line segments p1->p2, p2->p3, flipped
                // to bottom-left. fitz draws a single polyline with color black +
                // the given stroke width.
                let (p1, p2, p3) = (points[0], points[1], points[2]);
                let to_py = |y: f64| (page_h - y) as f32;
                let stroke = PdfPoints::new(*width as f32);
                let r1 = page.objects_mut().create_path_object_line(
                    PdfPoints::new(p1.0 as f32),
                    PdfPoints::new(to_py(p1.1)),
                    PdfPoints::new(p2.0 as f32),
                    PdfPoints::new(to_py(p2.1)),
                    PdfColor::BLACK,
                    stroke,
                );
                let r2 = page.objects_mut().create_path_object_line(
                    PdfPoints::new(p2.0 as f32),
                    PdfPoints::new(to_py(p2.1)),
                    PdfPoints::new(p3.0 as f32),
                    PdfPoints::new(to_py(p3.1)),
                    PdfColor::BLACK,
                    stroke,
                );
                match (r1, r2) {
                    (Ok(_), Ok(_)) => written += 1,
                    _ => logger::warning("stamp_plans: checkmark draw failed"),
                }
            }
            Plan::Signature { sid, rect } => {
                let png = match signature_pngs.get(sid) {
                    Some(b) if !b.is_empty() => b,
                    // No image for this sid (matches Python `if not png: continue`).
                    _ => continue,
                };
                let img = match image::load_from_memory(png) {
                    Ok(i) => i,
                    Err(e) => {
                        logger::warning(&format!(
                            "stamp_plans: signature decode failed for {sid}: {e}"
                        ));
                        continue;
                    }
                };
                // rect is TOP-LEFT [x0,y0,x1,y1]; placement origin in pdfium is
                // the bottom-left corner (x0, page_h - y1). keep_proportion via a
                // width/height scaled to the box while preserving aspect.
                let (x0, y0, x1, y1) = (rect[0], rect[1], rect[2], rect[3]);
                let box_w = (x1 - x0).max(0.0);
                let box_h = (y1 - y0).max(0.0);
                let (iw, ih) = {
                    use image::GenericImageView;
                    let (w, h) = img.dimensions();
                    (w as f64, h as f64)
                };
                let (draw_w, draw_h) = if iw > 0.0 && ih > 0.0 && box_w > 0.0 && box_h > 0.0 {
                    // Aspect-preserving fit (fitz keep_proportion=True).
                    let scale = (box_w / iw).min(box_h / ih);
                    (iw * scale, ih * scale)
                } else {
                    (box_w, box_h)
                };
                let py = page_h - y0 - draw_h;
                match page.objects_mut().create_image_object(
                    PdfPoints::new(x0 as f32),
                    PdfPoints::new(py as f32),
                    &img,
                    Some(PdfPoints::new(draw_w as f32)),
                    Some(PdfPoints::new(draw_h as f32)),
                ) {
                    Ok(_) => written += 1,
                    Err(e) => logger::warning(&format!(
                        "stamp_plans: signature stamp failed for {sid}: {e:?}"
                    )),
                }
            }
        }

        // Make the new page objects part of the saved content. The default
        // regeneration strategy already regenerates on every change, but we
        // request it explicitly so the save is deterministic.
        if let Err(e) = page.regenerate_content() {
            logger::warning(&format!(
                "stamp_plans: regenerate page {page_no}: {e:?}"
            ));
        }
    }

    doc.save_to_file(output_path)
        .map_err(|e| unavailable(format!("save {output_path}: {e:?}")))?;
    Ok(written)
}

/// Burn PNG signature images into AcroForm widget rects, keyed by field name.
///
/// Faithful port of `pdf_forms.stamp_signatures` (src/pdf_forms.py L219-L256):
/// PyMuPDF iterates `page.widgets()`, and for each widget whose `field_name` is
/// present in `stamps` calls `page.insert_image(w.rect, stream=png,
/// keep_proportion=True, overlay=True)`. We reproduce that with pdfium: walk the
/// `Widget` annotations on every page, read the form field name (pdfium's
/// `FPDFAnnot_GetFormFieldName` yields the fully-qualified name, matching
/// PyMuPDF's `field_name`) and the annotation `bounds()` (already in PDF points,
/// bottom-left origin — no y-flip needed, unlike `stamp_plans` whose rects come
/// from the editor's top-left percentages), then stamp the PNG aspect-preserved
/// and centered in the widget box (PyMuPDF `keep_proportion=True` centers).
///
/// Returns the number of stamps written (Python's `written` counter). The widget
/// annotation itself is left intact (still a form field) — the image is added as
/// an overlay page object, matching `overlay=True`. On a pdfium provisioning or
/// save failure returns `Err` (the caller logs the honest marker + returns 0,
/// never a fake count).
pub fn stamp_field_images(
    pdf_path: &str,
    output_path: &str,
    stamps: &HashMap<String, Vec<u8>>,
) -> Result<i64, String> {
    use image::GenericImageView;

    let pdfium = pdfium()?;
    let doc = pdfium
        .load_pdf_from_file(pdf_path, None)
        .map_err(|e| unavailable(format!("open {pdf_path}: {e:?}")))?;

    let page_count = doc.pages().len();
    let mut written: i64 = 0;

    for page_index in 0..page_count {
        let mut page = match doc.pages().get(page_index) {
            Ok(p) => p,
            Err(e) => {
                logger::warning(&format!(
                    "stamp_field_images: page {page_index} unavailable: {e:?}"
                ));
                continue;
            }
        };

        // Collect (field_name, widget rect) for widgets whose name is stamped.
        // The annotations borrow ends before we mutate the page's objects.
        let mut targets: Vec<(String, PdfRect)> = Vec::new();
        for annotation in page.annotations().iter() {
            if let PdfPageAnnotation::Widget(widget) = &annotation {
                let name = widget.form_field().and_then(|f| f.name());
                if let Some(name) = name {
                    if stamps.contains_key(&name) {
                        if let Ok(rect) = widget.bounds() {
                            targets.push((name, rect));
                        }
                    }
                }
            }
        }
        if targets.is_empty() {
            continue;
        }

        for (name, rect) in targets {
            // Python: `if not png: continue` — skip empty/missing rasters.
            let png = match stamps.get(&name) {
                Some(b) if !b.is_empty() => b,
                _ => continue,
            };
            let img = match image::load_from_memory(png) {
                Ok(i) => i,
                Err(e) => {
                    logger::warning(&format!(
                        "stamp_field_images: decode failed for {name}: {e}"
                    ));
                    continue;
                }
            };
            let box_w = (rect.right().value - rect.left().value).max(0.0) as f64;
            let box_h = (rect.top().value - rect.bottom().value).max(0.0) as f64;
            let (iw, ih) = {
                let (w, h) = img.dimensions();
                (w as f64, h as f64)
            };
            // keep_proportion=True: aspect-preserving fit, centered in the box.
            let (draw_w, draw_h) = if iw > 0.0 && ih > 0.0 && box_w > 0.0 && box_h > 0.0 {
                let scale = (box_w / iw).min(box_h / ih);
                (iw * scale, ih * scale)
            } else {
                (box_w, box_h)
            };
            let cx = (rect.left().value as f64 + rect.right().value as f64) / 2.0;
            let cy = (rect.bottom().value as f64 + rect.top().value as f64) / 2.0;
            let ox = cx - draw_w / 2.0;
            let oy = cy - draw_h / 2.0;
            match page.objects_mut().create_image_object(
                PdfPoints::new(ox as f32),
                PdfPoints::new(oy as f32),
                &img,
                Some(PdfPoints::new(draw_w as f32)),
                Some(PdfPoints::new(draw_h as f32)),
            ) {
                Ok(_) => written += 1,
                Err(e) => logger::warning(&format!(
                    "stamp_field_images: stamp failed for {name}: {e:?}"
                )),
            }
        }

        if let Err(e) = page.regenerate_content() {
            logger::warning(&format!(
                "stamp_field_images: regenerate page {page_index}: {e:?}"
            ));
        }
    }

    doc.save_to_file(output_path)
        .map_err(|e| unavailable(format!("save {output_path}: {e:?}")))?;
    Ok(written)
}

/// Extract embedded raster images from the requested pages, capped per page.
///
/// Supports `document_processor::_process_pdf`'s per-page image -> VL-OCR loop
/// (Python L120-L142), which does `images = list(page.images)` (pypdf) on pages
/// with little text and OCRs the first three. We reproduce `page.images` with
/// pdfium: walk each requested page's page objects, keep the image objects, and
/// decode each to a `DynamicImage` via `get_raw_image` (the as-stored raster,
/// closest to pypdf's `img.image`). Returns a map from 0-based page index to its
/// decoded images (only pages that actually carry ≥1 image are inserted, so a
/// present key mirrors Python's truthy `if images`).
///
/// BACKEND NOTE: pdfium enumerates image objects in content-stream order while
/// pypdf enumerates the page's image XObject resources; the set is the same but
/// the order (hence the cosmetic `image N` index) may differ. The OCR *content*
/// is unaffected. A per-image decode failure is logged and skipped (parity with
/// Python's per-image `try/except: continue`); a pdfium provisioning/open
/// failure returns `Err` (the caller degrades to "no images", never a panic).
pub fn extract_page_images(
    pdf_path: &str,
    pages_wanted: &std::collections::HashSet<usize>,
    cap_per_page: usize,
) -> Result<HashMap<usize, Vec<image::DynamicImage>>, String> {
    let pdfium = pdfium()?;
    let doc = pdfium
        .load_pdf_from_file(pdf_path, None)
        .map_err(|e| unavailable(format!("open {pdf_path}: {e:?}")))?;

    let mut out: HashMap<usize, Vec<image::DynamicImage>> = HashMap::new();
    let page_count = doc.pages().len();
    for page_index in 0..page_count {
        let idx = page_index as usize;
        if !pages_wanted.contains(&idx) {
            continue;
        }
        let page = match doc.pages().get(page_index) {
            Ok(p) => p,
            Err(_) => continue,
        };
        let mut imgs: Vec<image::DynamicImage> = Vec::new();
        for object in page.objects().iter() {
            if imgs.len() >= cap_per_page {
                break;
            }
            if let Some(image_obj) = object.as_image_object() {
                match image_obj.get_raw_image() {
                    Ok(img) => imgs.push(img),
                    Err(e) => logger::warning(&format!(
                        "extract_page_images: page {page_index} image decode failed: {e:?}"
                    )),
                }
            }
        }
        if !imgs.is_empty() {
            out.insert(idx, imgs);
        }
    }
    Ok(out)
}

// ---------------------------------------------------------------------------
// E — positioned-text label inference  (called by pdf_forms::page_words)
// ---------------------------------------------------------------------------

/// One positioned word: `(x0,y0,x1,y1,text)` in fitz TOP-LEFT space (y-down), so
/// the EXISTING `_infer_label` arithmetic (left/above the widget rect) ports
/// 1:1 without sign juggling.
#[derive(Debug, Clone, PartialEq)]
pub struct PositionedWord {
    pub x0: f64,
    pub y0: f64,
    pub x1: f64,
    pub y1: f64,
    pub text: String,
}

/// Positioned words for the 0-based `page_index_0based` of `pdf_path`, in fitz
/// TOP-LEFT space.
///
/// pdfium text segment bounds are BOTTOM-LEFT; this flips each segment so the
/// label arithmetic compares like-for-like:
///   `y0_tl = page_h - bounds.top()` (the visual TOP), `y1_tl = page_h -
///   bounds.bottom()` (the visual BOTTOM); `x0 = bounds.left()`, `x1 =
///   bounds.right()`.
///
/// pdfium segments are already word-ish runs (close enough to fitz `"words"`
/// granularity for label picking). When a segment text contains internal
/// whitespace we split it into tokens and distribute the segment bounds evenly
/// by character width (a faithful-enough approximation — fitz's per-word boxes
/// are tighter but the label picker only needs ordering + the left/above test).
pub fn page_words(pdf_path: &str, page_index_0based: i64) -> Result<Vec<PositionedWord>, String> {
    if page_index_0based < 0 {
        return Ok(Vec::new());
    }
    let pdfium = pdfium()?;
    let doc = pdfium
        .load_pdf_from_file(pdf_path, None)
        .map_err(|e| unavailable(format!("open {pdf_path}: {e:?}")))?;
    let pages = doc.pages();
    if page_index_0based >= pages.len() as i64 {
        return Ok(Vec::new());
    }
    let page = pages
        .get(page_index_0based as PdfPageIndex)
        .map_err(|e| unavailable(format!("page {page_index_0based}: {e:?}")))?;
    let page_h = page.height().value as f64;

    let text = page
        .text()
        .map_err(|e| unavailable(format!("page text: {e:?}")))?;

    let mut out = Vec::new();
    for segment in text.segments().iter() {
        let raw = segment.text();
        let trimmed = raw.trim();
        if trimmed.is_empty() {
            continue;
        }
        let bounds = segment.bounds();
        let left = bounds.left().value as f64;
        let right = bounds.right().value as f64;
        // Flip BOTTOM-LEFT -> TOP-LEFT.
        let y0_tl = page_h - bounds.top().value as f64; // visual top
        let y1_tl = page_h - bounds.bottom().value as f64; // visual bottom

        // Most segments are a single word-ish run. If the segment contains
        // internal whitespace, split into tokens and distribute the x-range by
        // proportional character width (y stays the segment's).
        if trimmed.split_whitespace().count() <= 1 {
            out.push(PositionedWord {
                x0: left,
                y0: y0_tl,
                x1: right,
                y1: y1_tl,
                text: trimmed.to_string(),
            });
            continue;
        }

        let total_chars: usize = raw.chars().count().max(1);
        let span = (right - left).max(0.0);
        // Walk the raw string tracking the cumulative char offset of each token
        // so each token's x-range is proportional to its position in the run.
        let mut char_cursor = 0usize;
        let mut idx = 0usize;
        let chars: Vec<char> = raw.chars().collect();
        while idx < chars.len() {
            // Skip leading whitespace.
            while idx < chars.len() && chars[idx].is_whitespace() {
                idx += 1;
                char_cursor += 1;
            }
            if idx >= chars.len() {
                break;
            }
            let token_start = char_cursor;
            let mut token = String::new();
            while idx < chars.len() && !chars[idx].is_whitespace() {
                token.push(chars[idx]);
                idx += 1;
                char_cursor += 1;
            }
            if token.is_empty() {
                continue;
            }
            let token_end = char_cursor;
            let tx0 = left + span * (token_start as f64 / total_chars as f64);
            let tx1 = left + span * (token_end as f64 / total_chars as f64);
            out.push(PositionedWord {
                x0: tx0,
                y0: y0_tl,
                x1: tx1,
                y1: y1_tl,
                text: token,
            });
        }
    }
    Ok(out)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    /// The cache root resolves under `DATA_DIR/pdfium` (no network).
    #[test]
    fn cache_dir_resolves_under_data_dir() {
        let root = cache_dir();
        assert!(root.ends_with("pdfium"), "got {root}");
    }

    /// The platform asset + member resolve for the host triple (mac/linux/win on
    /// supported arches); on any other host this is the honest "no prebuilt"
    /// Err. We only assert it does not panic and that, when Ok, the asset is a
    /// `.tgz` and the member matches the platform library family.
    #[test]
    fn platform_asset_is_consistent() {
        match platform_asset() {
            Ok((asset, member)) => {
                assert!(asset.ends_with(".tgz"), "asset {asset}");
                assert!(
                    member == "libpdfium.dylib"
                        || member == "libpdfium.so"
                        || member == "pdfium.dll",
                    "member {member}"
                );
            }
            Err(msg) => assert!(msg.contains("no prebuilt"), "{msg}"),
        }
    }

    /// `part_path` appends `.part` to the library file name (atomic-rename scratch).
    #[test]
    fn part_path_appends_suffix() {
        let p = PathBuf::from("/tmp/pdfium/libpdfium.dylib");
        let part = part_path(&p);
        assert_eq!(
            part.file_name().and_then(|n| n.to_str()),
            Some("libpdfium.dylib.part")
        );
        assert_eq!(part.parent(), p.parent());
    }

    /// `unavailable` carries the cause and the stable prefix HTTP handlers map to
    /// the honest 500.
    #[test]
    fn unavailable_message_shape() {
        let m = unavailable("boom");
        assert!(m.contains("PDFium could not be provisioned"));
        assert!(m.contains("boom"));
    }

    /// `PageGeom`/`PositionedWord` carry the documented public shape (compile +
    /// equality smoke).
    #[test]
    fn value_types_round_trip() {
        let g = PageGeom {
            page: 1,
            width: 1224,
            height: 1584,
        };
        assert_eq!(
            g,
            PageGeom {
                page: 1,
                width: 1224,
                height: 1584
            }
        );
        let w = PositionedWord {
            x0: 1.0,
            y0: 2.0,
            x1: 3.0,
            y1: 4.0,
            text: "Name".to_string(),
        };
        assert_eq!(w.text, "Name");
    }

    // `RenderOutcome::OutOfRange` is produced by an out-of-range page number
    // WITHOUT touching pdfium IFF pdfium is already provisioned — but since
    // provisioning needs network we cannot assert that offline. Instead, the
    // live round-trip below (ignored) exercises the real render path.

    /// Live integration test (ignored: needs network for the one-time PDFium
    /// download + the bblanchon binary). Generates a 1-page PDF with lopdf,
    /// renders it at 2.0 scale, and asserts PNG magic bytes + width ==
    /// round(page_w * scale); also asserts `page_geometries` returns 1 page with
    /// the truncated dims. Gated `#[ignore]` exactly like the image_models live
    /// download test so offline CI stays green.
    #[test]
    #[ignore]
    fn render_and_geometry_on_real_pdf() {
        use lopdf::{dictionary, Document, Object, Stream};

        // Build a minimal 1-page US-Letter (612x792) PDF in a temp file.
        let mut doc = Document::with_version("1.5");
        let pages_id = doc.new_object_id();
        let content = b"BT /F1 24 Tf 72 700 Td (Hello PDFium) Tj ET".to_vec();
        let content_id =
            doc.add_object(Stream::new(dictionary! {}, content));
        let font_id = doc.add_object(dictionary! {
            "Type" => "Font",
            "Subtype" => "Type1",
            "BaseFont" => "Helvetica",
        });
        let resources_id = doc.add_object(dictionary! {
            "Font" => dictionary! { "F1" => font_id },
        });
        let page_id = doc.add_object(dictionary! {
            "Type" => "Page",
            "Parent" => pages_id,
            "Contents" => content_id,
            "Resources" => resources_id,
            "MediaBox" => vec![0.into(), 0.into(), 612.into(), 792.into()],
        });
        let pages = dictionary! {
            "Type" => "Pages",
            "Kids" => vec![page_id.into()],
            "Count" => 1,
        };
        doc.objects.insert(pages_id, Object::Dictionary(pages));
        let catalog_id = doc.add_object(dictionary! {
            "Type" => "Catalog",
            "Pages" => pages_id,
        });
        doc.trailer.set("Root", catalog_id);

        let dir = std::env::temp_dir();
        let pdf_path = dir.join("odysseus_pdf_render_test.pdf");
        let pdf_path_str = pdf_path.to_string_lossy().into_owned();
        doc.save(&pdf_path).expect("save test pdf");

        // A: render-page-png at scale 2.0.
        match render_page_png(&pdf_path_str, 1, 2.0) {
            RenderOutcome::Png(bytes) => {
                assert!(bytes.len() > 8, "png too small");
                assert_eq!(&bytes[..8], b"\x89PNG\r\n\x1a\n", "PNG magic bytes");
                // Decode to check width == round(612 * 2.0) = 1224.
                let img = image::load_from_memory(&bytes).expect("decode png");
                use image::GenericImageView;
                let (w, _h) = img.dimensions();
                // pdfium uses round() for the bitmap target; allow a 1px slack.
                assert!((w as i64 - 1224).abs() <= 1, "width {w} != ~1224");
            }
            RenderOutcome::OutOfRange => panic!("page 1 should be in range"),
            RenderOutcome::Unavailable(msg) => {
                panic!("pdfium unavailable (needs network on first run): {msg}")
            }
        }

        // Out-of-range page -> OutOfRange (not 500).
        assert!(matches!(
            render_page_png(&pdf_path_str, 99, 2.0),
            RenderOutcome::OutOfRange
        ));

        // B: page_geometries — 1 page, truncated dims int(612*2)=1224, int(792*2)=1584.
        let geoms = page_geometries(&pdf_path_str, 2.0).expect("geometries");
        assert_eq!(geoms.len(), 1);
        assert_eq!(geoms[0].page, 1);
        assert_eq!(geoms[0].width, 1224);
        assert_eq!(geoms[0].height, 1584);

        // page_dims — user units (no scale).
        let dims = page_dims(&pdf_path_str).expect("dims");
        assert_eq!(dims.len(), 1);
        assert!((dims[0].0 - 612.0).abs() < 1.0);
        assert!((dims[0].1 - 792.0).abs() < 1.0);

        // E: page_words — at least the "Hello" / "PDFium" tokens, in top-left space.
        let words = page_words(&pdf_path_str, 0).expect("words");
        assert!(
            words.iter().any(|w| w.text.contains("Hello") || w.text.contains("PDFium")),
            "expected the sample words, got {words:?}"
        );

        let _ = std::fs::remove_file(&pdf_path);
    }
}
