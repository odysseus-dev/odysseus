// src/pdf_forms.rs  <- src/pdf_forms.py
//! PDF AcroForm field detection and extraction.
//!
//! Used to decide whether an uploaded PDF should be treated as a fillable form
//! (routed to the pdf_form document type) versus a regular text PDF (routed
//! through document_processor._process_pdf).
//!
//! BACKEND SWAP (documented, deliberate): the Python uses PyMuPDF (`fitz`,
//! AGPL-3.0) for both reading and writing the AcroForm. PyMuPDF is not a
//! faithfully-portable Rust dependency, so this port uses `lopdf` (a pure-Rust
//! PDF reader/writer) for the AcroForm OBJECT operations and the sibling
//! `crate::src::pdf_render` module (pdfium-render over the prebuilt libpdfium)
//! for the RENDER/GEOMETRY/POSITIONED-TEXT operations PyMuPDF used to cover:
//!
//!   * `has_form_fields` — count non-signature Widget annotations (>=3 heuristic).
//!     PORT_PARTIAL via lopdf.
//!   * `extract_fields` — name/type/value/options/page/rect/required from the
//!     page Widget annotations + `/Parent` field-tree inheritance (lopdf), with
//!     REAL label inference (`_infer_label`) backed by `pdf_render::page_words`
//!     positioned text — see below.
//!   * `fill_fields` — set `/V` (+ checkbox `/AS`) on each named widget and set
//!     `/AcroForm /NeedAppearances true`. PORT_PARTIAL via lopdf.
//!   * `stamp_annotations` — REAL via `pdf_render::stamp_plans`: the per-annotation
//!     page-percent -> user-unit + font-metric arithmetic stays here
//!     (`plan_annotation`), and the resulting draw plans are BURNED onto the page
//!     by pdfium (text runs + checkmark polylines). See the D caveats below.
//!
//! NOW REAL (no longer stubbed) — backed by `pdf_render` (pdfium-render):
//!   * `_infer_label` — REAL. PyMuPDF used `page.get_text("words")` (positioned
//!     text). `pdf_render::page_words` supplies the same positioned words (in
//!     fitz TOP-LEFT space), and `infer_label` ports the Python L91-128
//!     left/above-nearest arithmetic 1:1. `extract_fields` flips the lopdf
//!     `/Rect` (PDF BOTTOM-LEFT) to TOP-LEFT (using the page height from
//!     `pdf_render::page_geometries`) BEFORE inference so the rect and the words
//!     live in the SAME coordinate space (correctness trap — see CAUTION at the
//!     call site). When `pdf_render` is unavailable (offline first run /
//!     unsupported platform) inference is skipped and the label falls back to the
//!     field name — the SAME fallback Python uses when both `/TU` and inference
//!     yield nothing. Honest, documented.
//!   * `stamp_annotations` — REAL via `pdf_render::stamp_plans`. The geometry plan
//!     (`plan_annotation`) is unchanged; the burn now happens. On a pdfium
//!     provisioning failure (offline / unsupported platform) it logs the standard
//!     `[PDF stamping not available ...]` marker and returns 0 — NEVER a fake
//!     written count, NEVER a panic. (Honest-failure parity with Python's
//!     `import fitz` ModuleNotFoundError on a host without PyMuPDF.)
//!
//! STILL CAVEATED (honest fidelity / flagged remainder):
//!   * `fill_fields` sets `/V` but CANNOT regenerate the field appearance
//!     streams (`/AP`) the way PyMuPDF's `w.update()` does. We instead set
//!     `/AcroForm /NeedAppearances true` so a conforming viewer regenerates the
//!     appearance on open (and the `render-pdf` preview path rasterizes with
//!     pdfium `render_form_data(true)`, so filled previews show correct glyphs
//!     regardless). A viewer that ignores `NeedAppearances` shows stale
//!     appearances. Documented fidelity caveat, NOT a fake success. Routing fill
//!     through pdfium appearance regeneration (feature F) is INTENTIONALLY left
//!     for a later pass to avoid churning the already-verified lopdf `/V` path.
//!   (`stamp_signatures` and the `signature` annotation kind — PNG image-object
//!   stamping — are now REAL too: `stamp_signatures` via
//!   `pdf_render::stamp_field_images` and the `signature` plan via
//!   `pdf_render::stamp_plans`, both using pdfium `create_image_object`. On a
//!   pdfium provisioning failure they log the standard marker and return 0,
//!   never a fake count.)

use lopdf::{Dictionary, Document, Object, ObjectId, StringFormat};
use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{Map, Value};
use std::collections::HashMap;

use crate::pylog as logger;
use crate::src::pdf_render;

// ---------------------------------------------------------------------------
// _SIGNATURE_NAME_RE  (src/pdf_forms.py L52)
// ---------------------------------------------------------------------------
//
// Text widgets that are really signature placeholders. Covers DocuSign-style
// "_es_:signature" and the bare "signed N" / "Signature" patterns common in
// UK conveyancing forms (TA6, TA10). Uses substring match deliberately —
// false positives like "assigned" are rare in form-field names.
static SIGNATURE_NAME_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)sign(?:ed|ature)").unwrap());

// ---------------------------------------------------------------------------
// AcroForm field-bit flags (PDF spec 12.7.3.1)
// ---------------------------------------------------------------------------
//
// `required` = bool(field_flags & 2) in the Python — bit position 2 (value 2)
// of the `/Ff` flags is "Required". Field-type names mirror PyMuPDF's
// PDF_WIDGET_TYPE_* -> string map in `_widget_type_names`.

// ---------------------------------------------------------------------------
// Low-level lopdf helpers (resolve field-tree inheritance)
// ---------------------------------------------------------------------------

/// Decode a PDF text string (`/T`, `/V`, on-state names) to a Rust `String`.
///
/// AcroForm strings are usually PDFDocEncoding (Latin-1-ish) or UTF-16BE (with a
/// BOM). We handle the UTF-16BE BOM case explicitly and otherwise treat the
/// bytes as Latin-1, which is the correct decoding for the ASCII field names in
/// the real forms (TA6/TA10) and a lossless superset for arbitrary bytes.
fn decode_pdf_string(bytes: &[u8]) -> String {
    if bytes.len() >= 2 && bytes[0] == 0xFE && bytes[1] == 0xFF {
        // UTF-16BE with BOM.
        let units: Vec<u16> = bytes[2..]
            .chunks_exact(2)
            .map(|c| u16::from_be_bytes([c[0], c[1]]))
            .collect();
        return String::from_utf16_lossy(&units);
    }
    // PDFDocEncoding / Latin-1: each byte is a code point.
    bytes.iter().map(|&b| b as char).collect()
}

/// Resolve a value, following a single `/Reference` indirection if present.
fn deref<'a>(doc: &'a Document, obj: &'a Object) -> &'a Object {
    doc.dereference(obj).map(|(_, o)| o).unwrap_or(obj)
}

/// Read an inherited key from a field/widget dict, walking up the `/Parent`
/// chain (terminal AcroForm fields inherit `/FT`, `/T`, `/V`, `/Ff` from
/// ancestors). Mirrors how PyMuPDF surfaces the merged widget field properties.
fn inherited<'a>(doc: &'a Document, dict: &'a Dictionary, key: &[u8]) -> Option<&'a Object> {
    let mut cur: &Dictionary = dict;
    let mut depth = 0;
    loop {
        if let Ok(v) = cur.get(key) {
            return Some(deref(doc, v));
        }
        match cur.get(b"Parent") {
            Ok(p) => {
                let resolved = deref(doc, p);
                match resolved.as_dict() {
                    Ok(d) => cur = d,
                    Err(_) => return None,
                }
            }
            Err(_) => return None,
        }
        depth += 1;
        if depth > 32 {
            return None;
        }
    }
}

/// Fully-qualified field name: parent `/T` values joined to the field's own
/// `/T` with `.` (PDF spec 12.7.3.2). PyMuPDF's `field_name` is this FQN.
fn full_field_name(doc: &Document, dict: &Dictionary) -> String {
    // Collect from the widget up to the root, then join top-down with '.'.
    let mut parts: Vec<String> = Vec::new();
    let mut cur: &Dictionary = dict;
    let mut depth = 0;
    loop {
        if let Ok(t) = cur.get(b"T") {
            if let Ok(bytes) = deref(doc, t).as_str() {
                parts.push(decode_pdf_string(bytes));
            }
        }
        match cur.get(b"Parent") {
            Ok(p) => match deref(doc, p).as_dict() {
                Ok(d) => cur = d,
                Err(_) => break,
            },
            Err(_) => break,
        }
        depth += 1;
        if depth > 32 {
            break;
        }
    }
    parts.reverse();
    parts.join(".")
}

/// PyMuPDF widget-type name from the AcroForm `/FT` name + flags.
///
/// `/FT` is one of `/Btn` (button: checkbox/radio/pushbutton), `/Tx` (text),
/// `/Ch` (choice: listbox/combobox), `/Sig` (signature). We map to the same
/// string vocabulary `_widget_type_names` produces: text / checkbox / radio /
/// listbox / combobox / signature / button / unknown.
fn widget_type_name(doc: &Document, dict: &Dictionary) -> &'static str {
    let ft = inherited(doc, dict, b"FT").and_then(|o| o.as_name().ok());
    let flags = inherited(doc, dict, b"Ff")
        .and_then(|o| o.as_i64().ok())
        .unwrap_or(0);
    match ft {
        Some(b"Tx") => "text",
        Some(b"Sig") => "signature",
        Some(b"Ch") => {
            // /Ff bit 18 (value 1<<17 = 131072) = Combo.
            if flags & (1 << 17) != 0 {
                "combobox"
            } else {
                "listbox"
            }
        }
        Some(b"Btn") => {
            // /Ff bit 17 (value 1<<16 = 65536) = Radio; bit 16 (1<<15) = Pushbutton.
            if flags & (1 << 16) != 0 {
                "radio"
            } else if flags & (1 << 15) != 0 {
                "button"
            } else {
                "checkbox"
            }
        }
        _ => "unknown",
    }
}

/// True if a widget annotation is itself a signature field (`/FT /Sig`).
/// Used by `has_form_fields` to skip signature widgets.
fn is_signature_widget(doc: &Document, dict: &Dictionary) -> bool {
    inherited(doc, dict, b"FT")
        .and_then(|o| o.as_name().ok())
        .map(|n| n == b"Sig")
        .unwrap_or(false)
}

/// True if a dict is a `/Subtype /Widget` annotation.
fn is_widget(dict: &Dictionary) -> bool {
    dict.get(b"Subtype")
        .and_then(Object::as_name)
        .map(|n| n == b"Widget")
        .unwrap_or(false)
}

/// The on-state of a checkbox widget: the non-`Off` key under `/AP /N`.
/// Mirrors PyMuPDF's `w.on_state()`.
fn widget_on_state(doc: &Document, dict: &Dictionary) -> String {
    if let Ok(ap) = dict.get(b"AP") {
        if let Ok(ap_dict) = deref(doc, ap).as_dict() {
            if let Ok(n) = ap_dict.get(b"N") {
                if let Ok(n_dict) = deref(doc, n).as_dict() {
                    for (k, _) in n_dict.iter() {
                        if k != b"Off" {
                            return decode_pdf_string(k);
                        }
                    }
                }
            }
        }
    }
    String::new()
}

/// The field's `/V` value as a display string. Names (e.g. checkbox states)
/// and strings both reduce to text.
fn field_value_str(doc: &Document, dict: &Dictionary) -> String {
    match inherited(doc, dict, b"V") {
        Some(Object::String(b, _)) => decode_pdf_string(b),
        Some(Object::Name(n)) => decode_pdf_string(n),
        _ => String::new(),
    }
}

/// Choice `/Opt` values (combobox/listbox export values).
fn choice_values(doc: &Document, dict: &Dictionary) -> Vec<String> {
    match inherited(doc, dict, b"Opt") {
        Some(Object::Array(arr)) => arr
            .iter()
            .filter_map(|o| match deref(doc, o) {
                Object::String(b, _) => Some(decode_pdf_string(b)),
                // /Opt entries can be [export, display] pairs.
                Object::Array(pair) => pair.last().and_then(|x| match deref(doc, x) {
                    Object::String(b, _) => Some(decode_pdf_string(b)),
                    _ => None,
                }),
                _ => None,
            })
            .collect(),
        _ => Vec::new(),
    }
}

/// Flip a PDF-native BOTTOM-LEFT rect `[x0, y0, x1, y1]` (y grows up) to fitz
/// TOP-LEFT space (y grows down) given the page height in user units.
///
/// PyMuPDF's `w.rect` is top-left, so the Python stores top-left rects in the
/// sidecar and feeds top-left rects to `_infer_label`. We replicate that: the
/// x coordinates are unchanged; the y coordinates become `page_h - y` and the
/// top/bottom edges swap (the bottom-left's larger y == the top edge once
/// flipped) so the result is normalised with `y0 <= y1` (top <= bottom).
fn flip_rect_to_top_left(rect: [f64; 4], page_h: f64) -> [f64; 4] {
    let [x0, y0, x1, y1] = rect;
    // y0/y1 are the raw /Rect y values (lower-left, upper-right by convention,
    // but tolerate either order). After flipping, the smaller flipped value is
    // the TOP edge.
    let fy0 = page_h - y0;
    let fy1 = page_h - y1;
    [x0, fy0.min(fy1), x1, fy0.max(fy1)]
}

/// The widget `/Rect` as `[x0, y0, x1, y1]` floats (defaults to zeros).
fn widget_rect(doc: &Document, dict: &Dictionary) -> [f64; 4] {
    let mut rect = [0.0f64; 4];
    if let Ok(r) = dict.get(b"Rect") {
        if let Ok(arr) = deref(doc, r).as_array() {
            for (i, o) in arr.iter().take(4).enumerate() {
                rect[i] = deref(doc, o).as_float().map(|f| f as f64).unwrap_or(0.0);
            }
        }
    }
    rect
}

// ---------------------------------------------------------------------------
// _infer_label  (src/pdf_forms.py L91-L128) — REAL via pdf_render::page_words
// ---------------------------------------------------------------------------

/// Best-effort label inference from positioned text near a widget.
///
/// Faithful 1:1 port of the Python `_infer_label`. Strategy: prefer text
/// immediately to the LEFT on the same line, then text immediately ABOVE.
/// Returns the closest non-empty match or `""` if nothing useful is found.
///
/// COORDINATE SPACE: `words` and `rect` MUST both be in fitz TOP-LEFT space
/// (y grows DOWN). `pdf_render::page_words` already returns words in that space;
/// the caller in `extract_fields` flips the lopdf `/Rect` (PDF bottom-left) to
/// top-left before calling this, so the `y0 < y1` (top above bottom) ordering
/// the Python relies on holds for both. `rect` is `[x0, y0, x1, y1]` with `y0`
/// the TOP edge and `y1` the BOTTOM edge (matching `fitz.Rect`).
fn infer_label(words: &[pdf_render::PositionedWord], rect: &[f64; 4]) -> String {
    let (rx0, ry0, rx1, ry1) = (rect[0], rect[1], rect[2], rect[3]);
    // rect.height — fitz.Rect.height is (y1 - y0) in top-left space.
    let rect_height = ry1 - ry0;
    let line_tol = (2.0_f64).max(rect_height * 0.6);

    // (dist, wx0, text) tuples — mirrors the Python candidate triples.
    let mut candidates_left: Vec<(f64, f64, String)> = Vec::new();
    let mut candidates_above: Vec<(f64, f64, String)> = Vec::new();

    for w in words {
        let (wx0, wy0, wx1, wy1) = (w.x0, w.y0, w.x1, w.y1);
        let text = &w.text;
        if text.trim().is_empty() {
            continue;
        }
        // Same line, to the left.
        if ((wy0 + wy1) / 2.0 - (ry0 + ry1) / 2.0).abs() < line_tol && wx1 <= rx0 + 1.0 {
            candidates_left.push((rx0 - wx1, wx0, text.clone()));
        }
        // Above, horizontally overlapping (Python `elif`).
        else if wy1 <= ry0 + 1.0 && !(wx1 < rx0 || wx0 > rx1) {
            candidates_above.push((ry0 - wy1, wx0, text.clone()));
        }
    }

    let label = join_nearest(&mut candidates_left, 200.0, line_tol);
    if !label.is_empty() {
        return label;
    }
    join_nearest(&mut candidates_above, 40.0, line_tol)
}

/// Port of the nested `_join_nearest(cands, gap_limit)` closure: sort by
/// `(dist, wx0)`, bail if the nearest candidate is farther than `gap_limit`,
/// then join every candidate within `line_tol` of the nearest distance (sorted
/// by `wx0`, left-to-right) into one space-joined label.
fn join_nearest(cands: &mut [(f64, f64, String)], gap_limit: f64, line_tol: f64) -> String {
    if cands.is_empty() {
        return String::new();
    }
    // cands.sort(key=lambda c: (c[0], c[1]))
    cands.sort_by(|a, b| {
        a.0.partial_cmp(&b.0)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then(a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal))
    });
    let nearest_dist = cands[0].0;
    if nearest_dist > gap_limit {
        return String::new();
    }
    // same = [c for c in cands if c[0] - nearest_dist < line_tol]
    let mut same: Vec<&(f64, f64, String)> = cands
        .iter()
        .filter(|c| c.0 - nearest_dist < line_tol)
        .collect();
    // same.sort(key=lambda c: c[1])
    same.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal));
    same.iter()
        .map(|c| c.2.as_str())
        .collect::<Vec<_>>()
        .join(" ")
        .trim()
        .to_string()
}

// ---------------------------------------------------------------------------
// has_form_fields  (src/pdf_forms.py L55-L88)
// ---------------------------------------------------------------------------

/// Return True if the PDF looks like a *fillable form* — not just a content PDF
/// that happens to carry a stray widget.
///
/// Heuristic: require at least 3 non-signature widgets (mirrors the Python
/// comment). Signature-only PDFs read as content; tiny stray-widget counts no
/// longer hijack the chat.
///
/// PORT NOTE: PyMuPDF's `page.widgets()` yields one entry per widget annotation;
/// we iterate page Widget annotations (`/Subtype /Widget`) the same way and
/// short-circuit at 3 non-signature widgets exactly like the Python.
pub fn has_form_fields(path: &str) -> bool {
    let doc = match Document::load(path) {
        Ok(d) => d,
        Err(e) => {
            logger::warning(&format!(
                "Could not open PDF {path} for form detection: {e}"
            ));
            return false;
        }
    };
    let mut non_signature_count = 0;
    for (_pno, page_id) in doc.get_pages() {
        let annots = match doc.get_page_annotations(page_id) {
            Ok(a) => a,
            Err(_) => continue,
        };
        for w in annots {
            if !is_widget(w) {
                continue;
            }
            if !is_signature_widget(&doc, w) {
                non_signature_count += 1;
                if non_signature_count >= 3 {
                    return true;
                }
            }
        }
    }
    false
}

// ---------------------------------------------------------------------------
// extract_fields  (src/pdf_forms.py L138-L216)
// ---------------------------------------------------------------------------

/// Enumerate form fields, one entry per unique field name.
///
/// Multiple checkbox widgets sharing a field name are treated as a single
/// "choice" field whose options are each widget's on-state — that's the PDF
/// idiom for radio-style "Included / Excluded / None" rows.
///
/// Returns dicts with: name, type, label, value, options, page (1-indexed),
/// rect (x0,y0,x1,y1) for the first widget in the group, required.
///
/// LABEL INFERENCE (now REAL): PyMuPDF exposes `field_label` (`/TU`) and, when
/// empty, `_infer_label` runs a text-geometry pass over `page.get_text("words")`.
/// `pdf_render::page_words` supplies the same positioned words, so the label is
/// taken from `/TU` when present, else inferred from nearby text, else the field
/// name — the SAME three-tier fallback Python uses. When `pdf_render` is
/// unavailable (offline first run / unsupported platform) inference is skipped
/// and the label falls straight to the field name (graceful, honest).
///
/// RECT SPACE (correctness trap — fixed): lopdf `/Rect` is PDF BOTTOM-LEFT
/// (y-up), but PyMuPDF's `w.rect` (which the Python writes into both the stored
/// `rect` field AND feeds to `_infer_label`) is TOP-LEFT (y-down). We flip each
/// widget rect to top-left using the page height from `pdf_render::page_geometries`
/// BEFORE storing it and before inference, so (a) the stored sidecar rect matches
/// the Python's top-left rect that `render-pages` scales for the viewer overlay,
/// and (b) the rect and the `page_words` live in the SAME space. If page dims are
/// unavailable (pdfium not provisioned) we keep the raw lopdf rect and skip
/// inference — degraded but honest, never wrong-in-a-hidden-way.
pub fn extract_fields(path: &str) -> Vec<Value> {
    let doc = match Document::load(path) {
        Ok(d) => d,
        Err(e) => {
            logger::error(&format!(
                "Could not open PDF {path} for field extraction: {e}"
            ));
            return Vec::new();
        }
    };

    // Per-page geometry (user units, scale 1.0) from pdfium — used to flip the
    // lopdf bottom-left /Rect to fitz top-left. page_geometries returns one
    // PageGeom per page with 1-based `page`; map it to height. On a pdfium
    // provisioning failure this is empty and we fall back to the raw rect + no
    // inference (the honest degradation; NEVER a panic).
    let page_heights: HashMap<i64, f64> = match pdf_render::page_geometries(path, 1.0) {
        Ok(geoms) => geoms
            .into_iter()
            .map(|g| (g.page, g.height as f64))
            .collect(),
        Err(e) => {
            logger::warning(&format!(
                "[PDF label inference unavailable] page geometry for {path} could not be \
read via pdfium ({e}); storing raw rects and using the field-name label fallback"
            ));
            HashMap::new()
        }
    };

    // Lazily-loaded positioned words per page (fitz top-left space), keyed by
    // 1-based page number — mirrors the Python `words = page.get_text("words")`
    // computed once per page. Only populated when a page actually carries
    // widgets that need inference. `None` = not yet loaded; `Some(vec)` = loaded
    // (possibly empty when pdfium is unavailable).
    let mut page_words_cache: HashMap<i64, Vec<pdf_render::PositionedWord>> = HashMap::new();

    // grouped[name] = field dict; order preserves first-seen field order.
    let mut grouped: HashMap<String, Map<String, Value>> = HashMap::new();
    let mut on_states_acc: HashMap<String, Vec<String>> = HashMap::new();
    let mut order: Vec<String> = Vec::new();

    for (pno, page_id) in doc.get_pages() {
        let annots = match doc.get_page_annotations(page_id) {
            Ok(a) => a,
            Err(_) => continue,
        };
        for w in annots {
            if !is_widget(w) {
                continue;
            }
            let name = full_field_name(&doc, w);
            if name.is_empty() {
                continue;
            }
            let mut wtype = widget_type_name(&doc, w).to_string();

            // Raw lopdf /Rect is PDF BOTTOM-LEFT. Flip to fitz TOP-LEFT (the space
            // PyMuPDF's w.rect uses and the space page_words / the stored sidecar
            // rect must agree on). If we have no page height (pdfium unavailable),
            // keep the raw rect — degraded but honest.
            let rect_raw = widget_rect(&doc, w);
            let rect = match page_heights.get(&(pno as i64)) {
                Some(&ph) => flip_rect_to_top_left(rect_raw, ph),
                None => rect_raw,
            };

            // field_label = (/TU or "").strip(); when empty, _infer_label runs
            // over the page's positioned words (fitz top-left), EXACTLY like the
            // Python. Falls through to the field-name fallback below when both
            // /TU and inference yield "".
            let mut label = inherited(&doc, w, b"TU")
                .and_then(|o| o.as_str().ok())
                .map(|b| decode_pdf_string(b).trim().to_string())
                .unwrap_or_default();
            if label.is_empty() && page_heights.contains_key(&(pno as i64)) {
                // Load this page's words once (Python: words = page.get_text("words")).
                let words = page_words_cache.entry(pno as i64).or_insert_with(|| {
                    // pdf_render uses 0-based page indices; pno is 1-based.
                    pdf_render::page_words(path, (pno as i64) - 1).unwrap_or_default()
                });
                if !words.is_empty() {
                    label = infer_label(words, &rect);
                }
            }

            let value = field_value_str(&doc, w);
            let on_state = if wtype == "checkbox" {
                widget_on_state(&doc, w)
            } else {
                String::new()
            };

            if !grouped.contains_key(&name) {
                // AdobeSign-style signature placeholders are stored as plain text
                // widgets but named with `_es_:signature`.
                if wtype == "text" && SIGNATURE_NAME_RE.is_match(&name) {
                    wtype = "signature".to_string();
                }
                order.push(name.clone());

                let opts: Vec<String> = {
                    let cv = choice_values(&doc, w);
                    if !cv.is_empty() {
                        cv
                    } else if !on_state.is_empty() {
                        vec![on_state.clone()]
                    } else {
                        Vec::new()
                    }
                };

                // required = bool((field_flags or 0) & 2).
                let flags = inherited(&doc, w, b"Ff")
                    .and_then(|o| o.as_i64().ok())
                    .unwrap_or(0);

                let mut g = Map::new();
                g.insert("name".to_string(), Value::String(name.clone()));
                g.insert("type".to_string(), Value::String(wtype.clone()));
                // label = /TU, else inferred from positioned text (above), else
                // the field-name fallback (the Rust port keeps the never-empty
                // label guarantee even when inference also yields "").
                g.insert(
                    "label".to_string(),
                    Value::String(if label.is_empty() {
                        name.clone()
                    } else {
                        label.clone()
                    }),
                );
                g.insert("value".to_string(), Value::String(value.clone()));
                g.insert(
                    "options".to_string(),
                    Value::Array(opts.iter().map(|s| Value::String(s.clone())).collect()),
                );
                g.insert("page".to_string(), Value::from(pno as i64));
                g.insert(
                    "rect".to_string(),
                    Value::Array(rect.iter().map(|&f| json_f64(f)).collect()),
                );
                g.insert("required".to_string(), Value::Bool(flags & 2 != 0));

                on_states_acc.insert(
                    name.clone(),
                    if on_state.is_empty() {
                        Vec::new()
                    } else {
                        vec![on_state.clone()]
                    },
                );
                grouped.insert(name.clone(), g);
            } else {
                let g = grouped.get_mut(&name).unwrap();
                // if not g["label"] and label: g["label"] = label
                // PORT NOTE: the Rust grouped["label"] is never empty (it always
                // got the field-name fallback on first insert). To preserve the
                // Python semantics ("upgrade a placeholder label to a real /TU
                // label"), upgrade only when the current label equals the
                // field-name fallback AND a real /TU label is now available.
                if !label.is_empty() {
                    let cur_label = g.get("label").and_then(|v| v.as_str()).unwrap_or("");
                    if cur_label.is_empty() || cur_label == name {
                        g.insert("label".to_string(), Value::String(label.clone()));
                    }
                }
                // if value and not g["value"]: g["value"] = value
                if !value.is_empty() {
                    let cur_value = g.get("value").and_then(|v| v.as_str()).unwrap_or("");
                    if cur_value.is_empty() {
                        g.insert("value".to_string(), Value::String(value.clone()));
                    }
                }
                if !on_state.is_empty() {
                    let states = on_states_acc.get_mut(&name).unwrap();
                    if !states.contains(&on_state) {
                        states.push(on_state.clone());
                        // if on_state not in g["options"]: g["options"].append(on_state)
                        if let Some(Value::Array(opts)) = g.get_mut("options") {
                            let exists = opts.iter().any(|o| o.as_str() == Some(on_state.as_str()));
                            if !exists {
                                opts.push(Value::String(on_state.clone()));
                            }
                        }
                    }
                    // Promote a multi-on-state checkbox to a choice field.
                    if wtype == "checkbox" && states.len() > 1 {
                        g.insert("type".to_string(), Value::String("choice".to_string()));
                    }
                }
            }
        }
    }

    order
        .into_iter()
        .filter_map(|name| grouped.remove(&name).map(Value::Object))
        .collect()
}

/// `serde_json::Number` from an `f64` (Null on NaN/Inf — never produced by valid
/// PDF rects).
fn json_f64(v: f64) -> Value {
    serde_json::Number::from_f64(v)
        .map(Value::Number)
        .unwrap_or(Value::Null)
}

// ---------------------------------------------------------------------------
// stamp_signatures  (src/pdf_forms.py L219-L256) — REAL via pdf_render::stamp_field_images
// ---------------------------------------------------------------------------

/// Stamp PNG signature images into the PDF at each named field's rect.
///
/// REAL (no longer a stub): PyMuPDF's `page.insert_image(w.rect, stream=png,
/// keep_proportion=True, overlay=True)` is reproduced by
/// `pdf_render::stamp_field_images` (pdfium), which walks each page's `Widget`
/// annotations, matches the fully-qualified form-field name against `stamps`,
/// and burns the PNG aspect-preserved + centered into the widget `bounds()`
/// (already PDF-point bottom-left, so no y-flip). The widget stays a live form
/// field; the image is added as an overlay object — matching `overlay=True`.
///
/// Returns the number of stamps written (Python's `written`). On a pdfium
/// provisioning/save failure we log the standard `[PDF stamping not available]`
/// marker and return 0 — NEVER a fake count, NEVER a panic — mirroring Python's
/// `import fitz` failure on a host without PyMuPDF.
///
/// `stamps` is `{field_name: png_bytes}`.
pub fn stamp_signatures(
    pdf_path: &str,
    output_path: &str,
    stamps: &HashMap<String, Vec<u8>>,
) -> i64 {
    // Python returns 0 immediately on empty stamps; we match that (no marker
    // logged for the no-op-empty case, matching the early `return 0`).
    if stamps.is_empty() {
        return 0;
    }
    match pdf_render::stamp_field_images(pdf_path, output_path, stamps) {
        Ok(count) => count,
        Err(e) => {
            logger::warning(&format!(
                "[PDF stamping not available] stamp_signatures: PDFium burn failed \
({e}); returning 0 (never a fake count)"
            ));
            0
        }
    }
}

// ---------------------------------------------------------------------------
// stamp_annotations  (src/pdf_forms.py L259-L365) — REAL via pdf_render::stamp_plans
// ---------------------------------------------------------------------------

/// One freeform annotation's resolved geometry + draw plan, in PDF user units.
///
/// The page-percent -> user-unit + font-metric arithmetic from the Python is
/// ported faithfully here (`plan_annotation`); the actual burn is performed by
/// `pdf_render::stamp_plans` (pdfium), which consumes these plans. Coordinates
/// are in fitz TOP-LEFT space (y from the top); `stamp_plans` flips y to pdfium
/// bottom-left at draw time (a shared-contract agreement — see the module doc).
#[derive(Debug, Clone, PartialEq)]
pub enum AnnotationDrawPlan {
    /// `kind == "text"`: one `insert_text` per line, starting at `(xx, yy)` and
    /// advancing `yy` by `line_box` between lines (fontsize fixed at 11.0).
    Text {
        xx: f64,
        yy: f64,
        line_box: f64,
        fontsize: f64,
        lines: Vec<String>,
    },
    /// `kind == "check"`: a 3-point checkmark polyline (in user units) with a
    /// stroke width.
    Check {
        points: [(f64, f64); 3],
        width: f64,
    },
    /// `kind == "signature"`: draw the PNG for `sid` into `rect` (x0,y0,x1,y1).
    Signature {
        sid: String,
        rect: [f64; 4],
    },
    /// The annotation contributed no drawable output (e.g. empty text, a
    /// signature value not prefixed with `signature:`). Skipped, like Python's
    /// `continue`.
    Skip,
}

/// Compute the (pure) draw plan for one annotation given the page dimensions in
/// user units. Faithful port of the per-annotation arithmetic in the Python
/// `stamp_annotations` loop. `page_count` bounds-checks the page number.
///
/// Returns `None` if the annotation's page is out of range (Python `continue`).
pub fn plan_annotation(
    ann: &Map<String, Value>,
    pw: f64,
    ph: f64,
    page_count: i64,
) -> Option<AnnotationDrawPlan> {
    // page_no = int(ann.get("page") or 1)
    let page_no = ann.get("page").and_then(json_to_i64).unwrap_or(0);
    let page_no = if page_no == 0 { 1 } else { page_no };
    if page_no < 1 || page_no > page_count {
        return None;
    }
    let x = json_to_f64(ann.get("x")).unwrap_or(0.0) / 100.0 * pw;
    let y = json_to_f64(ann.get("y")).unwrap_or(0.0) / 100.0 * ph;
    let w = json_to_f64(ann.get("w")).unwrap_or(0.0) / 100.0 * pw;
    let h = json_to_f64(ann.get("h")).unwrap_or(0.0) / 100.0 * ph;
    // kind = ann.get("kind", "text")
    let kind = ann
        .get("kind")
        .and_then(|v| v.as_str())
        .unwrap_or("text")
        .to_string();
    let value = ann.get("value").and_then(|v| v.as_str()).unwrap_or("");

    if kind == "text" {
        if value.is_empty() {
            return Some(AnnotationDrawPlan::Skip);
        }
        // line_height = float(ann.get("line_height") or 1.3)
        let line_height = match json_to_f64(ann.get("line_height")) {
            Some(v) if v != 0.0 => v,
            _ => 1.3,
        };
        let lines: Vec<String> = value.split('\n').map(|s| s.to_string()).collect();
        let fontsize = 11.0_f64;
        let line_box = fontsize * line_height * 1.2;
        // First baseline at one ascent below the box top.
        let yy = y + fontsize * 0.85;
        // Match the textarea's 4px left padding (~3 PDF points).
        let xx = x + 3.0;
        Some(AnnotationDrawPlan::Text {
            xx,
            yy,
            line_box,
            fontsize,
            lines,
        })
    } else if kind == "check" {
        let cx = x + w / 2.0;
        let cy = y + h / 2.0;
        let size = w.min(h) * 0.85;
        let p1 = (cx - size * 0.40, cy + size * 0.05);
        let p2 = (cx - size * 0.10, cy + size * 0.30);
        let p3 = (cx + size * 0.45, cy - size * 0.30);
        let width = (1.0_f64).max(size * 0.13);
        Some(AnnotationDrawPlan::Check {
            points: [p1, p2, p3],
            width,
        })
    } else if kind == "signature" {
        if !value.starts_with("signature:") {
            return Some(AnnotationDrawPlan::Skip);
        }
        let sid = value["signature:".len()..].trim().to_string();
        Some(AnnotationDrawPlan::Signature {
            sid,
            rect: [x, y, x + w, y + h],
        })
    } else {
        // Unknown kind — Python falls through all branches and just bumps no
        // counter; treat as a skip.
        Some(AnnotationDrawPlan::Skip)
    }
}

/// Burn freeform annotations (text, check, signature) onto a PDF.
///
/// REAL (no longer a stub): the per-annotation geometry is computed via
/// `plan_annotation` (ported faithfully, fitz top-left units) and the resulting
/// draw plans are BURNED onto the page by `pdf_render::stamp_plans` (pdfium —
/// text runs + checkmark polylines; signature image draw is delegated and may be
/// skipped there, flagged at that boundary). Returns the number of annotations
/// actually drawn (parity with the Python `written` counter).
///
/// PAGE DIMENSIONS: `plan_annotation` needs each page's width/height in user
/// units. We read them once via `pdf_render::page_geometries(pdf_path, 1.0)`
/// (scale 1.0 -> user units). On a pdfium provisioning failure (offline first
/// run / unsupported platform) we log the standard `[PDF stamping not
/// available ...]` marker and return 0 — NEVER a fake count, NEVER a panic. This
/// mirrors Python's `import fitz` ModuleNotFoundError on a host without PyMuPDF.
///
/// `signature_pngs` is `{sid: png_bytes}` for the `signature` annotation kind.
pub fn stamp_annotations(
    pdf_path: &str,
    output_path: &str,
    annotations: &[Value],
    signature_pngs: Option<&HashMap<String, Vec<u8>>>,
) -> i64 {
    // Python returns 0 immediately on empty annotations (no save, no marker).
    if annotations.is_empty() {
        return 0;
    }

    // Per-page user-unit dimensions (scale 1.0). Err == pdfium unavailable ->
    // honest marker + 0 (never a fake count). page_geometries returns one entry
    // per page with 1-based `page`.
    let geoms = match pdf_render::page_geometries(pdf_path, 1.0) {
        Ok(g) => g,
        Err(e) => {
            logger::warning(&format!(
                "[PDF stamping not available] stamp_annotations: PDFium could not be \
provisioned ({e}); returning 0 (no annotations drawn, never a fake count)"
            ));
            return 0;
        }
    };
    let page_count = geoms.len() as i64;
    // dims[1-based page] = (pw, ph).
    let dims: HashMap<i64, (f64, f64)> = geoms
        .iter()
        .map(|g| (g.page, (g.width as f64, g.height as f64)))
        .collect();

    // Build the (page_no, plan) list, dropping out-of-range pages (Python
    // `continue`) and `Skip` plans (empty text / unprefixed signature / unknown
    // kind — Python falls through without bumping `written`).
    let mut plans: Vec<(i64, AnnotationDrawPlan)> = Vec::new();
    for ann in annotations {
        let ann_map = match ann.as_object() {
            Some(m) => m,
            None => continue,
        };
        // page_no = int(ann.get("page") or 1)  — same coercion plan_annotation uses.
        let raw_page = ann_map.get("page").and_then(json_to_i64).unwrap_or(0);
        let page_no = if raw_page == 0 { 1 } else { raw_page };
        let (pw, ph) = match dims.get(&page_no) {
            Some(&d) => d,
            // Page out of range for the actual document -> Python `continue`.
            None => continue,
        };
        match plan_annotation(ann_map, pw, ph, page_count) {
            Some(AnnotationDrawPlan::Skip) | None => continue,
            Some(plan) => plans.push((page_no, plan)),
        }
    }

    let empty_pngs: HashMap<String, Vec<u8>> = HashMap::new();
    let pngs = signature_pngs.unwrap_or(&empty_pngs);

    // Delegate the burn. stamp_plans also SAVES output_path (even with an empty
    // plan list, so the downstream pipeline's `current_out` always exists), and
    // returns the real drawn count. An Err here means pdfium failed mid-op ->
    // honest marker + 0.
    match pdf_render::stamp_plans(pdf_path, output_path, &plans, pngs) {
        Ok(count) => count,
        Err(e) => {
            logger::warning(&format!(
                "[PDF stamping not available] stamp_annotations: PDFium burn failed \
({e}); returning 0 (never a fake count)"
            ));
            0
        }
    }
}

// ---------------------------------------------------------------------------
// fill_fields  (src/pdf_forms.py L368-L401)  — PORT_PARTIAL via lopdf
// ---------------------------------------------------------------------------

/// Write values back into the AcroForm and save a new PDF.
///
/// Returns the number of fields updated. Unknown field names are ignored.
/// Layout of the source PDF is preserved.
///
/// PORT NOTE (appearance caveat): PyMuPDF's `w.update()` regenerates each
/// field's appearance stream (`/AP`). lopdf cannot do that, so we set `/V`
/// (+ checkbox `/AS`) and set the catalog `/AcroForm /NeedAppearances true` so a
/// conforming viewer regenerates appearances on open. A viewer that ignores
/// `NeedAppearances` will show stale appearances. Documented fidelity caveat —
/// the field VALUES are written for real (this is NOT a stub), only the
/// rendered glyphs may lag in non-conforming viewers.
///
/// `values` carries `bool` for single checkboxes and `String` otherwise (the
/// same shape `parse_markdown_to_values` produces).
pub fn fill_fields(source_path: &str, output_path: &str, values: &Map<String, Value>) -> i64 {
    let mut doc = match Document::load(source_path) {
        Ok(d) => d,
        Err(e) => {
            logger::error(&format!(
                "Could not open PDF {source_path} for fill_fields: {e}"
            ));
            return 0;
        }
    };

    // Pass 1 (immutable borrow): collect the per-widget mutation plan so we can
    // mutate `doc` afterwards without aliasing. PyMuPDF iterates `page.widgets()`
    // and mutates each in place; lopdf borrow rules force the collect-then-mutate
    // split, but the observable result (each matching widget gets its /V set) is
    // identical.
    struct Plan {
        widget_id: ObjectId,
        // The /V value to write (a PDF text string).
        v_string: String,
        // For checkboxes, also the /AS appearance-state name to write.
        as_name: Option<String>,
        // True when /V should be written as a Name (checkbox on-state / Off),
        // false for a literal text string.
        v_is_name: bool,
    }
    let mut plans: Vec<Plan> = Vec::new();

    for (_pno, page_id) in doc.get_pages() {
        // Re-collect the annotation object-ids from the page so we can resolve
        // each widget's own ObjectId for mutation (get_page_annotations returns
        // borrowed dicts, not their ids).
        let widget_ids = collect_widget_ids(&doc, page_id);
        for wid in widget_ids {
            let w = match doc.get_dictionary(wid) {
                Ok(d) => d,
                Err(_) => continue,
            };
            let name = full_field_name(&doc, w);
            let new_value = match values.get(&name) {
                Some(v) => v,
                None => continue,
            };
            let ftype = widget_type_name(&doc, w);
            if ftype == "checkbox" {
                let on_state = widget_on_state(&doc, w);
                let chosen_state: String = match new_value {
                    Value::Bool(b) => {
                        // Single checkbox: bool semantics.
                        if *b {
                            if on_state.is_empty() {
                                "Yes".to_string()
                            } else {
                                on_state.clone()
                            }
                        } else {
                            "Off".to_string()
                        }
                    }
                    _ => {
                        // Choice/radio group: only the widget whose on_state
                        // matches gets that on_state; the rest go Off.
                        let chosen = value_to_chosen_str(new_value);
                        if !on_state.is_empty() && on_state == chosen {
                            on_state.clone()
                        } else {
                            "Off".to_string()
                        }
                    }
                };
                plans.push(Plan {
                    widget_id: wid,
                    v_string: chosen_state.clone(),
                    as_name: Some(chosen_state),
                    v_is_name: true,
                });
            } else {
                // w.field_value = "" if new_value is None else str(new_value)
                let v = match new_value {
                    Value::Null => String::new(),
                    other => value_to_chosen_str(other),
                };
                plans.push(Plan {
                    widget_id: wid,
                    v_string: v,
                    as_name: None,
                    v_is_name: false,
                });
            }
        }
    }

    let mut updated: i64 = 0;
    for plan in &plans {
        if let Ok(w) = doc.get_dictionary_mut(plan.widget_id) {
            if plan.v_is_name {
                w.set("V", Object::Name(plan.v_string.clone().into_bytes()));
            } else {
                w.set(
                    "V",
                    Object::String(plan.v_string.clone().into_bytes(), StringFormat::Literal),
                );
            }
            if let Some(as_name) = &plan.as_name {
                w.set("AS", Object::Name(as_name.clone().into_bytes()));
            }
            updated += 1;
        }
    }

    // Set /AcroForm /NeedAppearances true so a conforming viewer regenerates the
    // (now stale) appearance streams — the closest lopdf equivalent to PyMuPDF's
    // per-widget w.update() appearance regeneration. Documented caveat.
    set_need_appearances(&mut doc);

    // Python: doc.save(output_path, incremental=False, deflate=True). lopdf's
    // save() writes the full document; we do not request stream compression (the
    // observable field values are unaffected by deflate).
    if let Err(e) = doc.save(output_path) {
        logger::error(&format!("Failed to save filled PDF {output_path}: {e}"));
        return 0;
    }
    updated
}

/// Collect the ObjectIds of `/Subtype /Widget` annotations on a page.
fn collect_widget_ids(doc: &Document, page_id: ObjectId) -> Vec<ObjectId> {
    let mut ids: Vec<ObjectId> = Vec::new();
    let page = match doc.get_dictionary(page_id) {
        Ok(p) => p,
        Err(_) => return ids,
    };
    let annots = match page.get(b"Annots") {
        Ok(a) => deref(doc, a),
        Err(_) => return ids,
    };
    if let Ok(arr) = annots.as_array() {
        for o in arr {
            if let Ok(id) = o.as_reference() {
                if let Ok(d) = doc.get_dictionary(id) {
                    if is_widget(d) {
                        ids.push(id);
                    }
                }
            }
        }
    }
    ids
}

/// `str(new_value).strip()`-equivalent for the JSON value shapes that appear in
/// the fill map (string/bool/number). For checkbox choice-group matching the
/// Python strips; the text path uses `str(new_value)` without stripping, but the
/// markdown values are already trimmed by `parse_markdown_to_values`, so the
/// observable result matches.
fn value_to_chosen_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.trim().to_string(),
        Value::Bool(b) => {
            if *b {
                "True".to_string()
            } else {
                "False".to_string()
            }
        }
        Value::Number(n) => n.to_string(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

/// Set the catalog `/AcroForm /NeedAppearances true`.
fn set_need_appearances(doc: &mut Document) {
    // Resolve the AcroForm dict's ObjectId (it is normally an indirect ref under
    // the catalog /AcroForm). If the AcroForm is inline or absent we skip — a PDF
    // with fillable fields always carries an AcroForm, so the indirect path is
    // the real one.
    let acro_id: Option<ObjectId> = doc
        .catalog()
        .ok()
        .and_then(|cat| cat.get(b"AcroForm").ok().cloned())
        .and_then(|o| o.as_reference().ok());
    if let Some(id) = acro_id {
        if let Ok(acro) = doc.get_dictionary_mut(id) {
            acro.set("NeedAppearances", Object::Boolean(true));
        }
    }
}

fn json_to_f64(v: Option<&Value>) -> Option<f64> {
    match v {
        Some(Value::Number(n)) => n.as_f64(),
        Some(Value::String(s)) => s.parse::<f64>().ok(),
        _ => None,
    }
}

fn json_to_i64(v: &Value) -> Option<i64> {
    match v {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
        Value::String(s) => s.parse::<i64>().ok(),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn ann(v: Value) -> Map<String, Value> {
        v.as_object().unwrap().clone()
    }

    fn word(x0: f64, y0: f64, x1: f64, y1: f64, t: &str) -> pdf_render::PositionedWord {
        pdf_render::PositionedWord {
            x0,
            y0,
            x1,
            y1,
            text: t.to_string(),
        }
    }

    /// stamp_signatures and stamp_annotations return 0 — NEVER a fake count,
    /// NEVER a panic — both on the empty early-return AND when PDFium cannot
    /// open the source (offline provisioning OR a missing file: the pdfium
    /// open/`page_geometries` Errs and we log the marker + return 0). Both image
    /// draws ARE real now (pdfium `create_image_object`); the honest-failure
    /// contract is what this test pins.
    #[test]
    fn stamp_helpers_never_fake_a_count_and_never_panic() {
        // Empty inputs -> 0 (the early-return path, no marker, no pdfium touch).
        assert_eq!(stamp_signatures("x.pdf", "out.pdf", &HashMap::new()), 0);
        assert_eq!(stamp_annotations("x.pdf", "out.pdf", &[], None), 0);

        // Non-empty signature stamps against a non-existent source: pdfium open
        // (or provisioning) Errs -> honest 0, never a panic, never a fake count.
        let mut stamps = HashMap::new();
        stamps.insert("sig1".to_string(), vec![1u8, 2, 3]);
        assert_eq!(
            stamp_signatures("/nonexistent/x.pdf", "/tmp/odys_stamp_out.pdf", &stamps),
            0
        );

        // Non-empty annotations against a non-existent path: page_geometries Errs
        // (offline pdfium OR missing file) -> honest 0, never a panic, never a
        // fabricated count.
        let anns = vec![json!({
            "id": "a1", "page": 1, "x": 10.0, "y": 20.0, "w": 30.0, "h": 5.0,
            "kind": "text", "value": "hi", "line_height": 1.3
        })];
        assert_eq!(
            stamp_annotations("/nonexistent/x.pdf", "/tmp/out.pdf", &anns, None),
            0
        );
    }

    /// `flip_rect_to_top_left` maps a PDF bottom-left rect to fitz top-left and
    /// normalises top<=bottom.
    #[test]
    fn flip_rect_bottom_left_to_top_left() {
        // Page height 792. A widget whose bottom-left /Rect is [100, 700, 200, 720]
        // (y up) sits NEAR THE TOP of the page; in top-left space its top edge is
        // 792-720=72 and bottom edge 792-700=92.
        let tl = flip_rect_to_top_left([100.0, 700.0, 200.0, 720.0], 792.0);
        assert!((tl[0] - 100.0).abs() < 1e-9);
        assert!((tl[1] - 72.0).abs() < 1e-9); // top
        assert!((tl[2] - 200.0).abs() < 1e-9);
        assert!((tl[3] - 92.0).abs() < 1e-9); // bottom
        assert!(tl[1] <= tl[3], "top edge must be <= bottom edge");
    }

    /// `infer_label` prefers the NEAREST word to the LEFT on the same line and
    /// joins only words within `line_tol` of that nearest distance — the faithful
    /// Python L114-123 `_join_nearest` behaviour (a far-left word at a much larger
    /// distance does NOT get joined). All coords are fitz top-left space.
    #[test]
    fn infer_label_prefers_nearest_left_on_same_line() {
        // Widget rect (top-left): x0=120 y0=100 x1=220 y1=116 (height 16 ->
        // line_tol = max(2, 16*0.6) = 9.6).
        let rect = [120.0, 100.0, 220.0, 116.0];
        let words = vec![
            // "Full" ends far from the widget (dist 120-70 = 50).
            word(40.0, 101.0, 70.0, 115.0, "Full"),
            // "Name:" is adjacent (dist 120-118 = 2) -> the nearest, wins alone:
            // 50 - 2 = 48 > line_tol(9.6) so "Full" is NOT joined.
            word(75.0, 101.0, 118.0, 115.0, "Name:"),
            // A far-above heading that must NOT win over the left match.
            word(120.0, 10.0, 200.0, 24.0, "Section"),
        ];
        assert_eq!(infer_label(&words, &rect), "Name:");
    }

    /// Two left words at the SAME distance from the widget (e.g. one stacked
    /// above the other, both ending at the same x) DO get joined left-to-right
    /// by `wx0`, exactly like the Python `same` clustering.
    #[test]
    fn infer_label_joins_left_words_within_line_tol() {
        let rect = [120.0, 100.0, 220.0, 116.0];
        let words = vec![
            // Both end at x=118 (dist 2 each), centred on the widget line, so both
            // land in `same`; sorted by wx0 -> "First" then "Last".
            word(60.0, 101.0, 118.0, 108.0, "Last"),
            word(20.0, 105.0, 118.0, 112.0, "First"),
        ];
        // Sorted by wx0: First(20) < Last(60) -> "First Last".
        assert_eq!(infer_label(&words, &rect), "First Last");
    }

    /// When nothing is to the left, `infer_label` falls back to the word ABOVE
    /// (within the 40pt gap limit) — Python L111/L128 path.
    #[test]
    fn infer_label_falls_back_to_above() {
        let rect = [120.0, 100.0, 220.0, 116.0];
        let words = vec![
            // Directly above, horizontally overlapping, within 40pt.
            word(120.0, 78.0, 190.0, 92.0, "Address"),
        ];
        assert_eq!(infer_label(&words, &rect), "Address");
    }

    /// Words farther than the gap limit yield no label (Python `nearest_dist >
    /// gap_limit -> ""`); both left (>200) and above (>40) are rejected.
    #[test]
    fn infer_label_respects_gap_limits() {
        let rect = [400.0, 100.0, 500.0, 116.0];
        let words = vec![
            // Left but 250pt away (gap_limit 200) -> rejected.
            word(100.0, 101.0, 150.0, 115.0, "TooFarLeft"),
            // Above but 60pt away (gap_limit 40) -> rejected.
            word(400.0, 30.0, 480.0, 40.0, "TooFarAbove"),
        ];
        assert_eq!(infer_label(&words, &rect), "");
    }

    /// No words -> empty label.
    #[test]
    fn infer_label_empty_words_is_empty() {
        let rect = [10.0, 10.0, 50.0, 26.0];
        assert_eq!(infer_label(&[], &rect), "");
    }

    #[test]
    fn plan_text_annotation_geometry() {
        // Page 612x792 (US Letter). x=10% y=20% w=30% h=10%.
        let a = ann(json!({
            "page": 1, "x": 10.0, "y": 20.0, "w": 30.0, "h": 10.0,
            "kind": "text", "value": "line1\nline2", "line_height": 1.3
        }));
        let plan = plan_annotation(&a, 612.0, 792.0, 1).unwrap();
        match plan {
            AnnotationDrawPlan::Text {
                xx,
                yy,
                line_box,
                fontsize,
                lines,
            } => {
                let x = 10.0 / 100.0 * 612.0; // 61.2
                let y = 20.0 / 100.0 * 792.0; // 158.4
                assert_eq!(fontsize, 11.0);
                assert!((xx - (x + 3.0)).abs() < 1e-9);
                assert!((yy - (y + 11.0 * 0.85)).abs() < 1e-9);
                assert!((line_box - (11.0 * 1.3 * 1.2)).abs() < 1e-9);
                assert_eq!(lines, vec!["line1".to_string(), "line2".to_string()]);
            }
            other => panic!("expected Text, got {other:?}"),
        }
    }

    #[test]
    fn plan_empty_text_is_skip() {
        let a = ann(json!({"page": 1, "x": 0, "y": 0, "w": 10, "h": 10, "kind": "text", "value": ""}));
        assert_eq!(
            plan_annotation(&a, 100.0, 100.0, 1),
            Some(AnnotationDrawPlan::Skip)
        );
    }

    #[test]
    fn plan_check_geometry() {
        let a = ann(json!({"page": 1, "x": 0.0, "y": 0.0, "w": 100.0, "h": 100.0, "kind": "check"}));
        // pw=ph=100 -> x=y=0, w=h=100. size = min(100,100)*0.85 = 85.
        let plan = plan_annotation(&a, 100.0, 100.0, 1).unwrap();
        match plan {
            AnnotationDrawPlan::Check { points, width } => {
                let cx = 50.0;
                let cy = 50.0;
                let size = 85.0;
                assert!((points[0].0 - (cx - size * 0.40)).abs() < 1e-9);
                assert!((points[2].1 - (cy - size * 0.30)).abs() < 1e-9);
                assert!((width - (1.0_f64).max(size * 0.13)).abs() < 1e-9);
            }
            other => panic!("expected Check, got {other:?}"),
        }
    }

    #[test]
    fn plan_signature_requires_prefix() {
        let bad = ann(json!({"page": 1, "x": 0, "y": 0, "w": 10, "h": 10, "kind": "signature", "value": "nope"}));
        assert_eq!(
            plan_annotation(&bad, 100.0, 100.0, 1),
            Some(AnnotationDrawPlan::Skip)
        );
        let good = ann(json!({"page": 1, "x": 0, "y": 0, "w": 10, "h": 10, "kind": "signature", "value": "signature:  abc "}));
        match plan_annotation(&good, 100.0, 100.0, 1).unwrap() {
            AnnotationDrawPlan::Signature { sid, .. } => assert_eq!(sid, "abc"),
            other => panic!("expected Signature, got {other:?}"),
        }
    }

    #[test]
    fn plan_page_out_of_range_returns_none() {
        let a = ann(json!({"page": 5, "x": 0, "y": 0, "w": 10, "h": 10, "kind": "text", "value": "x"}));
        assert_eq!(plan_annotation(&a, 100.0, 100.0, 1), None);
        let a0 = ann(json!({"page": 0, "x": 0, "y": 0, "w": 10, "h": 10, "kind": "text", "value": "x"}));
        // page 0 -> coerced to 1, in range.
        assert!(plan_annotation(&a0, 100.0, 100.0, 1).is_some());
    }

    /// Write a minimal, valid single-page (612x792) PDF to `path` via lopdf so
    /// the live test has a real document to stamp. No content stream is needed —
    /// pdfium can open a blank page and we burn onto it.
    fn write_minimal_pdf(path: &std::path::Path) {
        use lopdf::dictionary;
        let mut doc = Document::with_version("1.5");
        let pages_id = doc.new_object_id();
        let page_id = doc.add_object(dictionary! {
            "Type" => "Page",
            "Parent" => pages_id,
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
        doc.save(path).expect("write minimal pdf");
    }

    /// LIVE test (network + pdfium): stamp a text + check annotation onto a real
    /// 1-page PDF and verify (a) the drawn count is exactly 2 and (b) the output
    /// parses back as a PDF via lopdf. Gated `#[ignore]` like image_models' live
    /// download test — the first PDFium op downloads ~5-6 MB from bblanchon
    /// GitHub releases, so offline CI stays green by default. Run with:
    ///   cargo test -- --ignored stamp_annotations_burns_on_real_pdf
    #[test]
    #[ignore]
    fn stamp_annotations_burns_on_real_pdf() {
        let dir = tempfile::tempdir().unwrap();
        let src = dir.path().join("src.pdf");
        let out = dir.path().join("stamped.pdf");
        write_minimal_pdf(&src);

        let anns = vec![
            json!({
                "id": "t1", "page": 1, "x": 10.0, "y": 20.0, "w": 40.0, "h": 8.0,
                "kind": "text", "value": "Hello PDFium", "line_height": 1.3
            }),
            json!({
                "id": "c1", "page": 1, "x": 60.0, "y": 60.0, "w": 5.0, "h": 5.0,
                "kind": "check"
            }),
        ];

        let drawn = stamp_annotations(
            src.to_str().unwrap(),
            out.to_str().unwrap(),
            &anns,
            None,
        );
        assert_eq!(drawn, 2, "expected one text + one check drawn");

        // The output must parse back as a valid PDF with one page.
        let reloaded = Document::load(&out).expect("stamped PDF must parse back");
        assert_eq!(reloaded.get_pages().len(), 1);
    }

    /// LIVE test (network + pdfium): `extract_fields` label inference path runs
    /// without panicking on a real (field-less) PDF. With no AcroForm fields the
    /// result is empty, but the pdfium page_geometries / page_words plumbing is
    /// exercised end-to-end. Gated `#[ignore]` (network).
    #[test]
    #[ignore]
    fn extract_fields_label_inference_plumbing_live() {
        let dir = tempfile::tempdir().unwrap();
        let src = dir.path().join("blank.pdf");
        write_minimal_pdf(&src);
        // No fields -> empty, but must not panic and must touch pdfium cleanly.
        let fields = extract_fields(src.to_str().unwrap());
        assert!(fields.is_empty());
    }
}
