// src/document_processor.rs  <- src/document_processor.py
//! Document processing: PDF/OCR extraction, text file handling, image VL
//! analysis, user content building.
//!
//! PORT_NOW. The pure file-handling slices and the vision-language (VL)
//! image-analysis layer are faithful ports (the VL call is fully wired — see
//! `analyze_image_with_vl_result` / `vl_chat`), and the per-page PDF *image*
//! OCR loop is now real too: embedded rasters are extracted via pdfium
//! (`pdf_render::extract_page_images`) and OCR'd through the VL layer.
//!
//! Per-function classification (mirrors the Python `src/document_processor.py`):
//!   * `_is_text_file`            — PORT_NOW (pure extension predicate).
//!   * `_process_text_file`       — PORT_NOW (char-based truncation loop +
//!                                   code-fence rendering). The Python first
//!                                   tries `personal_docs.read_text_file`
//!                                   (`encoding="utf-8", errors="ignore"`) and
//!                                   falls back to a raw read with
//!                                   `charset_normalizer` detection. Since
//!                                   `personal_docs.rs` is a sibling pass, the
//!                                   `read_text_file` behaviour is inlined here
//!                                   (lossy UTF-8 read = `errors="ignore"`).
//!   * `_process_pdf`             — PORT_NOW. Text extraction via the
//!                                   `pdf-extract` crate (`extract_text_by_pages`),
//!                                   reproducing the per-page `[Page N text]:`
//!                                   concatenation + the 15000-char cap. The
//!                                   per-page image -> VL-OCR loop is REAL:
//!                                   embedded rasters are extracted via pdfium
//!                                   (`pdf_render::extract_page_images`, capped
//!                                   at 3, mirroring `pypdf`'s `page.images[:3]`)
//!                                   and OCR'd through `analyze_image_with_vl`.
//!   * `_load_vl_settings` / `_resolve_vl_model` — PORT_NOW. The vision-model
//!                                   *resolution* chain is no longer deferred:
//!                                   `_load_vl_settings` calls the ported
//!                                   `settings::load_settings`, and
//!                                   `_resolve_vl_model` ports the Python
//!                                   `src/document_processor.py:164-188` on top of
//!                                   the already-ported
//!                                   `ai_interaction::resolve_model_spec`
//!                                   (`_resolve_model`) — the configured-model
//!                                   path plus the EXACT vision-capable candidate
//!                                   priority list. It returns the same
//!                                   `(url, model_id, headers)` tuple the Python
//!                                   does, so callers (e.g. the
//!                                   `ai_fill_annotations` route) can resolve a VL
//!                                   endpoint for real.
//!   * `analyze_image_with_vl_result` / `analyze_image_with_vl` — PORT_NOW. The
//!                                   vision *call* is now fully wired (port of
//!                                   `src/document_processor.py:191-245`): the
//!                                   `vision_enabled` gate, `_resolve_vl_model`,
//!                                   the OpenAI `image_url` content block, the
//!                                   `resolve_vision_fallback_candidates` fallback
//!                                   chain, and the real `llm_call_async`
//!                                   round-trip that decodes the description. The
//!                                   honest bracketed markers
//!                                   (`[Vision is disabled …]`,
//!                                   `[No vision model configured …]`,
//!                                   `[VL model unavailable - image not analyzed]`)
//!                                   are returned ONLY on the soft-fail paths
//!                                   Python takes — never a fabricated
//!                                   description. The public functions stay SYNC
//!                                   (their signatures are unchanged, so the
//!                                   `chat_handler` / `upload_routes` / `mod`
//!                                   callers are untouched); the async resolve +
//!                                   `llm_call_async` is driven via the
//!                                   `block_in_place`/`Handle::block_on` async->sync
//!                                   bridge (the `app_initializer` precedent).
//!   * `vl_chat`                  — PORT_NOW. The SHARED single-image vision
//!                                   message builder: assembles the OpenAI-shaped
//!                                   `[text block, image_url block]` user message
//!                                   (the ONE place that content block is built)
//!                                   and calls `llm_core::llm_call_async`, which
//!                                   routes OpenAI-vs-Anthropic internally via
//!                                   `_detect_provider`. Reused by the gallery
//!                                   ai-tag route and this module's doc-OCR path.
//!   * `build_user_content`       — PORT_PARTIAL. The str|list assembly is fully
//!                                   ported (base64 image/audio inlining, mime
//!                                   guessing, the OWNER-AWARE `resolve_upload`
//!                                   resolution + pre-resolved `resolved_uploads`
//!                                   map, the shared `MAX_INLINE_ATTACHMENT_CHARS`
//!                                   budget via `_fit_inline_attachment_text`, the
//!                                   Office/EPUB `_process_office_document` path,
//!                                   PDF-form detection -> pdf_forms + sidecar +
//!                                   pdf_form_doc, the auto_opened_docs DB SELECT).
//!                                   The only DEFERRED dependency it reaches
//!                                   transitively is the VL OCR inside
//!                                   `_process_pdf`.
//!   * `_truncate_inline` / `_fit_inline_attachment_text` /
//!     `_process_office_document` / `strip_pdf_content_marker` — PORT_NOW. The
//!                                   inline-budget helpers, the markitdown-
//!                                   equivalent Office/EPUB extractor (inlined:
//!                                   `markitdown_runtime.py` is not yet a Rust
//!                                   sibling, so DOCX/PPTX/XLSX/EPUB text is pulled
//!                                   from their ZIP/XML members via the in-tree
//!                                   `zip` crate — an HONEST simplification of
//!                                   markitdown's full Markdown structure), and the
//!                                   `removeprefix`-based PDF-marker strip.
//!
//! Whole module is `web`-gated: it pulls in `pdf-extract`/`mime_guess`/`base64`,
//! the `lopdf`-backed `pdf_forms`, the DB-backed `pdf_form_doc`, and the
//! `session_local()` SELECT — all of which are web-feature deps.


use base64::Engine as _;
use serde_json::{json, Map, Value};
use std::collections::HashMap;

use crate::pylog as logger;

// ---------------------------------------------------------------------------
// Module constants (src/document_processor.py L15-L16)
// ---------------------------------------------------------------------------

/// Shared inline-attachment budget. Individual processors already cap single
/// files, but multi-file batches can still add N capped bodies to one user turn;
/// this bounds the TOTAL inline attachment text across one `build_user_content`
/// call so a big batch can't blow the model's context.
pub const MAX_INLINE_ATTACHMENT_CHARS: usize = 24000;

/// Below this much remaining budget, the next attachment is omitted-by-name
/// rather than sliced (a tiny slice is useless), mirroring the Python sentinel.
pub const MIN_INLINE_ATTACHMENT_SLICE: usize = 500;

// ---------------------------------------------------------------------------
// UploadHandlerLike  (the `upload_handler` parameter of build_user_content)
// ---------------------------------------------------------------------------
//
// The Python `build_user_content(..., upload_handler, ...)` is handed the
// stateful `UploadHandler` instance and only calls a handful of its predicate
// methods. `upload_handler.rs` is a *sibling* parallel-unit (not yet landed in
// this pass), so to avoid a hard module dependency we accept any type that
// implements this small trait. When `upload_handler.rs` lands, its
// `UploadHandler` simply implements `UploadHandlerLike` (the method names and
// signatures match the Python surface 1:1).
//
// Note the Python `is_image_file` / `is_audio_file` / `is_document_file` take
// `(filename, content_type=None)`; the call sites here always pass the resolved
// mime as `content_type`, so the trait mirrors that with an `&str` argument
// (the Python `None` default path is exercised elsewhere, not by this caller).
pub trait UploadHandlerLike {
    /// `upload_handler.validate_upload_id(fid)` — format/whitelist check.
    fn validate_upload_id(&self, upload_id: &str) -> bool;
    /// `upload_handler.inside_base_dir(path)` — path-traversal guard.
    fn inside_base_dir(&self, path: &str) -> bool;
    /// `upload_handler._inside_upload_dir(path)` — containment check rooted at
    /// the upload dir. `build_user_content` prefers this when present (the real
    /// handler always exposes it), falling back to `inside_base_dir` otherwise.
    ///
    /// Default mirrors the Python `not hasattr(upload_handler, "_inside_upload_dir")`
    /// fallback to `inside_base_dir`; the real `UploadHandler` overrides it.
    fn inside_upload_dir(&self, path: &str) -> bool {
        self.inside_base_dir(path)
    }
    /// `upload_handler.is_image_file(filename, content_type)`.
    fn is_image_file(&self, filename: &str, content_type: &str) -> bool;
    /// `upload_handler.is_audio_file(filename, content_type)`.
    fn is_audio_file(&self, filename: &str, content_type: &str) -> bool;
    /// `upload_handler.is_document_file(filename, content_type)`.
    fn is_document_file(&self, filename: &str, content_type: &str) -> bool;
    /// `upload_handler.resolve_upload(fid, owner=owner)` — owner-aware metadata
    /// lookup (path/mime/name). Returns `None` when the id is unknown or the
    /// caller is not authorized to read it. The real handler's method takes the
    /// full `(upload_id, owner, auth_manager, allow_admin)` surface; this trait
    /// mirrors the exact `build_user_content` call site
    /// (`auth_manager=None, allow_admin=True`).
    ///
    /// Default returns `None` so test stubs that don't implement resolution fall
    /// back to nothing-resolved (the real `UploadHandler` overrides it). This is
    /// the Rust analogue of the Python `hasattr(upload_handler, "resolve_upload")`
    /// guard: stubs without it simply don't resolve.
    fn resolve_upload(&self, _upload_id: &str, _owner: Option<&str>) -> Option<Map<String, Value>> {
        None
    }
}

// ---------------------------------------------------------------------------
// _is_text_file  (src/document_processor.py L16-L21)
// ---------------------------------------------------------------------------

/// Check if file has text extension.
pub fn _is_text_file(path: &str) -> bool {
    let lower = path.to_lowercase();
    [
        ".txt", ".py", ".html", ".htm", ".md", ".json", ".csv", ".log", ".js",
    ]
    .iter()
    .any(|ext| lower.ends_with(ext))
}

// ---------------------------------------------------------------------------
// _process_text_file  (src/document_processor.py L24-L105)
// ---------------------------------------------------------------------------

/// Process text file with enhanced formatting and metadata.
///
/// Faithful port of the char-based truncation loop. Python first tries
/// `personal_docs.read_text_file` (UTF-8 with `errors="ignore"`), and only
/// falls back to the raw-read + `charset_normalizer` path if that import/call
/// fails. The Rust port reads bytes once and decodes with `from_utf8_lossy`,
/// which is the faithful equivalent of `errors="ignore"`/`errors="replace"`
/// (charset_normalizer auto-detection is an HONEST simplification — documented).
pub fn _process_text_file(path: &str) -> String {
    // language_map (L26-L33).
    let language_map: HashMap<&str, &str> = [
        (".py", "python"),
        (".js", "javascript"),
        (".html", "html"),
        (".css", "css"),
        (".json", "json"),
        (".md", "markdown"),
        (".txt", "text"),
        (".csv", "csv"),
        (".log", "log"),
        (".sh", "bash"),
        (".yml", "yaml"),
        (".yaml", "yaml"),
        (".xml", "xml"),
        (".sql", "sql"),
        (".cpp", "cpp"),
        (".c", "c"),
        (".java", "java"),
        (".go", "go"),
        (".rs", "rust"),
        (".php", "php"),
        (".rb", "ruby"),
        (".ts", "typescript"),
        (".jsx", "javascript"),
        (".tsx", "typescript"),
    ]
    .into_iter()
    .collect();

    let filename = crate::pyos::path::basename(path);
    // `os.path.splitext(path.lower())[1]`.
    let ext = splitext_ext(&path.to_lowercase());
    let language = *language_map.get(ext.as_str()).unwrap_or(&"text");
    let max_len: usize = if ext != ".log" { 30000 } else { 10000 };

    // `read_text_file(path)` (utf-8, errors="ignore") with the raw-read fallback.
    // Both Python branches end in lossy decode of the file bytes, so the Rust
    // port reads bytes once and decodes lossily. A read failure mirrors the
    // Python "[Failed to read attached file]" path.
    let content: String = match std::fs::read(path) {
        Ok(bytes) => String::from_utf8_lossy(&bytes).into_owned(),
        Err(e) => {
            logger::error(&format!("Failed to read file {path}: {e}"));
            return "\n\n[Failed to read attached file]".to_string();
        }
    };

    // file_size with thousands separators (`f"{file_size:,}"`).
    let size_str = match std::fs::metadata(path) {
        Ok(m) => group_thousands(m.len()),
        Err(_) => "unknown".to_string(),
    };

    // line_count = len(content.split("\n")); content_length = len(content) (chars).
    let line_count = content.split('\n').count();
    let chars: Vec<char> = content.chars().collect();
    let content_length = chars.len();
    let mut truncated = false;
    let mut content = content;

    if content_length > max_len {
        // Python's forward-then-backward newline search around `max_len`.
        let mut truncation_point = max_len;
        let search_range = std::cmp::min(100, content_length - max_len);
        // Python `for ... else`: the `else` (the backward search) runs ONLY if
        // the forward loop completed every iteration *without* a `break`. BOTH
        // the boundary guard (`>= content_length`) and the newline match `break`,
        // so any of those skips the else. Track whether the forward loop broke at
        // all; only run the backward search if it ran to completion.
        let mut forward_broke = false;
        for i in 0..search_range {
            if truncation_point + i >= content_length {
                forward_broke = true;
                break;
            }
            if chars[truncation_point + i] == '\n' {
                truncation_point += i;
                forward_broke = true;
                break;
            }
        }
        if !forward_broke {
            let back_range = std::cmp::min(100, truncation_point);
            for i in 0..back_range {
                // Python indexes content[truncation_point - i]; for i==0 that is
                // content[truncation_point] (one past the cut), matching source.
                let idx = truncation_point.wrapping_sub(i);
                if idx < content_length && chars[idx] == '\n' {
                    truncation_point -= i;
                    break;
                }
            }
        }
        content = chars[..truncation_point].iter().collect();
        // Python sets `truncated = True` unconditionally at L85 once this block
        // is entered (the inner-branch assignments are immediately overwritten),
        // so we set it once here — same observable result.
        truncated = true;
    }

    // header (L87-L88).
    let mut header = format!("\n=== File: {filename} ===\n");
    header.push_str(&format!(
        "[Type: {language}, Lines: {line_count}, Size: {size_str} bytes]"
    ));

    // code_extensions set (L90-L94).
    let code_extensions: &[&str] = &[
        ".py", ".js", ".html", ".css", ".json", ".md", ".sh", ".yml", ".yaml",
        ".xml", ".sql", ".cpp", ".c", ".java", ".go", ".rs", ".php", ".rb",
        ".ts", ".jsx", ".tsx",
    ];
    if code_extensions.contains(&ext.as_str()) {
        let mut code_block = format!("```{language}\n{content}");
        if truncated {
            code_block.push_str("\n[Truncated]");
        }
        code_block.push_str("\n```");
        format!("{header}\n\n{code_block}")
    } else {
        let mut result = format!("{header}\n\n{content}");
        if truncated {
            result.push_str("\n[Truncated]");
        }
        result
    }
}

// ---------------------------------------------------------------------------
// _process_pdf  (src/document_processor.py L108-L152)
// ---------------------------------------------------------------------------

/// Process PDF file with text extraction. Uses VL model for image-heavy pages.
///
/// PORT_PARTIAL. Text extraction is faithful via `pdf-extract`'s
/// `extract_text_by_pages`, reproducing the per-page `[Page N text]:` blocks and
/// the 15000-char `[PDF content truncated]` cap.
///
/// The per-page image -> VL-OCR loop (Python L120-L142) is now REAL: for every
/// page with <50 chars of extracted text, we pull its embedded raster images via
/// `pdf_render::extract_page_images` (pdfium, capped at 3 — mirroring pypdf's
/// `list(page.images)[:3]`), write each to a temp PNG, run the ported
/// `analyze_image_with_vl`, and append `[Page N image M text]: …` for any result
/// that is non-empty and does not contain "unavailable" (exact Python filter).
/// A pdfium provisioning/open failure degrades to "no images" (the text blocks
/// are unaffected) — never a panic, never fake OCR text.
pub fn _process_pdf(path: &str) -> String {
    // try: PdfReader(path); for page in reader.pages: ...
    let pages = match pdf_extract::extract_text_by_pages(path) {
        Ok(p) => p,
        Err(e) => {
            // Python: `except Exception as e: return f"\n\n[PDF processing failed: {e}]"`.
            return format!("\n\n[PDF processing failed: {e}]");
        }
    };

    // Pre-extract images (one pdfium open) for the pages that pass the
    // `len(page_text) < 50` gate — pdfium decoding only happens where Python's
    // `if images and len(page_text) < 50:` could fire. `len()` is char-based in
    // Python, so we count chars, not bytes.
    let need: std::collections::HashSet<usize> = pages
        .iter()
        .enumerate()
        .filter(|(_, p)| p.trim().chars().count() < 50)
        .map(|(i, _)| i)
        .collect();
    let images_by_page: std::collections::HashMap<usize, Vec<image::DynamicImage>> =
        if need.is_empty() {
            std::collections::HashMap::new()
        } else {
            match crate::src::pdf_render::extract_page_images(path, &need, 3) {
                Ok(m) => m,
                Err(e) => {
                    // pdfium unavailable -> the Python `try: images = list(page.images)
                    // except Exception: images = []` degrade-to-empty, just hoisted.
                    logger::warning(&format!(
                        "_process_pdf: embedded-image extraction unavailable ({e}); \
proceeding with text only"
                    ));
                    std::collections::HashMap::new()
                }
            }
        };

    let mut pdf_text = String::new();
    for (idx, page) in pages.iter().enumerate() {
        let page_num = idx + 1;
        let page_text = page.trim();
        if !page_text.is_empty() {
            pdf_text.push_str(&format!("\n\n[Page {page_num} text]:\n{page_text}"));
        }
        // if images and len(page_text) < 50:
        if page_text.chars().count() < 50 {
            if let Some(imgs) = images_by_page.get(&idx) {
                // for img_index, img in enumerate(images[:3]): (cap applied upstream)
                for (img_index, img) in imgs.iter().enumerate() {
                    // tempfile.NamedTemporaryFile(suffix=".png", delete=False);
                    // img.image.save(temp, "PNG"); ocr = analyze_image_with_vl(temp);
                    // finally: os.unlink(temp)  — a unique system-temp path (never
                    // the live data dir), removed after OCR like Python's finally.
                    static TMP_SEQ: std::sync::atomic::AtomicU64 =
                        std::sync::atomic::AtomicU64::new(0);
                    let seq = TMP_SEQ.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                    let temp_img_path = std::env::temp_dir()
                        .join(format!("odys_pdfimg_{}_{seq}.png", std::process::id()));
                    if let Err(e) = img.save_with_format(&temp_img_path, image::ImageFormat::Png) {
                        // Python's per-image `except Exception as e: ... continue`.
                        logger::warning(&format!("Failed to analyze image in PDF: {e}"));
                        let _ = std::fs::remove_file(&temp_img_path);
                        continue;
                    }
                    let ocr_text = analyze_image_with_vl(&temp_img_path.to_string_lossy());
                    let _ = std::fs::remove_file(&temp_img_path); // finally: os.unlink
                    if !ocr_text.is_empty() && !ocr_text.to_lowercase().contains("unavailable") {
                        pdf_text.push_str(&format!(
                            "\n\n[Page {page_num} image {} text]: {ocr_text}",
                            img_index + 1
                        ));
                    }
                }
            }
        }
    }

    if !pdf_text.is_empty() {
        // `if len(pdf_text) > 15000:` (char-based).
        let chars: Vec<char> = pdf_text.chars().collect();
        if chars.len() > 15000 {
            let head: String = chars[..15000].iter().collect();
            pdf_text = format!("{head}\n[PDF content truncated]");
        }
        format!("\n\n[PDF content]:{pdf_text}")
    } else {
        "\n\n[PDF processed but no readable content found]".to_string()
    }
}

// ---------------------------------------------------------------------------
// _truncate_inline  (src/document_processor.py L158-L163)
// ---------------------------------------------------------------------------

/// Cap inline document text so a huge file can't blow the model's context.
///
/// Returns `(body, marker)` where `marker` is the truncation banner (or `""`).
/// `len` is char-based (Python `len(str)` counts code points).
fn truncate_inline(text: &str, limit: usize) -> (String, &'static str) {
    let trimmed = text.trim();
    let chars: Vec<char> = trimmed.chars().collect();
    if chars.len() > limit {
        let head: String = chars[..limit].iter().collect();
        (head, "\n[…truncated for inline context.]")
    } else {
        (trimmed.to_string(), "")
    }
}

// ---------------------------------------------------------------------------
// _fit_inline_attachment_text  (src/document_processor.py L166-L198)
// ---------------------------------------------------------------------------

/// Fit extracted attachment text into the shared inline attachment budget.
///
/// Individual processors already cap single files, but multi-file batches can
/// still add N capped bodies to one user turn. Keep the first files readable,
/// keep later files visible by name, and mark exactly where inline content was
/// reduced so the model does not silently miss attachments.
///
/// Returns `(text, new_remaining)`. All length math is char-based (Python
/// `len(str)`); the banners embed the budget with comma thousands separators to
/// match the Python `f"{N:,}"` formatting.
fn fit_inline_attachment_text(
    text: &str,
    remaining: usize,
    display_name: &str,
) -> (String, usize) {
    let chars: Vec<char> = text.chars().collect();
    // if len(text) <= remaining: return text, remaining - len(text)
    if chars.len() <= remaining {
        return (text.to_string(), remaining - chars.len());
    }

    // name = os.path.basename(display_name or "attachment")
    let name = {
        let dn = if display_name.is_empty() {
            "attachment"
        } else {
            display_name
        };
        crate::pyos::path::basename(dn)
    };
    let budget = group_thousands(MAX_INLINE_ATTACHMENT_CHARS as u64);

    if remaining < MIN_INLINE_ATTACHMENT_SLICE {
        let msg = format!(
            "\n\n[Attachment omitted from inline context: {name}. \
             The {budget}-character shared inline attachment budget was already \
             used by earlier attachments. Ask to inspect this file specifically \
             if more detail is needed.]"
        );
        return (msg, 0);
    }

    let remaining_str = group_thousands(remaining as u64);
    let marker = format!(
        "\n\n[Attachment content truncated: {name}. \
         Only {remaining_str} characters of this attachment fit within the \
         {budget}-character shared inline attachment budget. Ask to inspect this \
         file specifically if more detail is needed.]"
    );
    let head: String = chars[..remaining].iter().collect();
    (format!("{head}{marker}"), 0)
}

// ---------------------------------------------------------------------------
// _process_office_document  (src/document_processor.py L201-L228)
// ---------------------------------------------------------------------------

/// Office/EPUB extensions routed through the markitdown-equivalent path. Mirrors
/// `src/markitdown_runtime.py::MARKITDOWN_EXTS` (PDFs stay on the pypdf path,
/// plain text/code/csv/json/markdown/html stay on the cheaper built-in text
/// path; these are the formats the text path otherwise drops entirely).
const MARKITDOWN_EXTS: &[&str] = &[".docx", ".pptx", ".xlsx", ".xls", ".epub"];

/// `is_markitdown_format(path)` — true if the extension is one we route through
/// the office-document extractor.
fn is_markitdown_format(path: &str) -> bool {
    let ext = splitext_ext(&path.to_lowercase());
    MARKITDOWN_EXTS.contains(&ext.as_str())
}

/// Extract an Office/EPUB document to Markdown (markitdown-equivalent).
///
/// Faithful port of `src/document_processor.py:201-228`. The Python delegates to
/// the optional `markitdown` dependency via `src/markitdown_runtime.py`; that
/// module is NOT yet a Rust sibling, so the conversion is inlined here (the only
/// faithful option while editing this file alone). DOCX/PPTX/XLSX/XLSX/EPUB are
/// ZIP containers whose embedded XML/text is extracted with the in-tree `zip`
/// crate. When no text can be extracted, the function returns the SAME friendly
/// banners the Python returns, so a missing/blank document never breaks the chat
/// path:
///   * non-markitdown ext      -> `"\n\n[Attached document file]"`
///   * extracted, has text     -> `"\n\n[Document content — <title>]:\n<body><marker>"`
///   * extractor present, blank -> `"\n\n[Attached document: <name> — no extractable text found.]"`
fn process_office_document(path: &str, display_name: &str) -> String {
    // if not is_markitdown_format(path): return "\n\n[Attached document file]"
    if !is_markitdown_format(path) {
        return "\n\n[Attached document file]".to_string();
    }

    // markdown = convert_to_markdown(path)
    let markdown = convert_office_to_markdown(path);

    // if markdown and markdown.strip():
    if let Some(md) = markdown.as_deref() {
        if !md.trim().is_empty() {
            // title = os.path.splitext(os.path.basename(path))[0]
            let basename = crate::pyos::path::basename(path);
            let title = splitext_root(&basename);
            let (body, marker) = truncate_inline(md, 15000);
            return format!("\n\n[Document content — {title}]:\n{body}{marker}");
        }
    }

    // No content. Python distinguishes "dep missing" (load_markitdown raises) from
    // "no extractable text" (dep present). The Rust extractor is always present
    // (the `zip` crate is in-tree), so the only path is the no-text banner.
    format!("\n\n[Attached document: {display_name} — no extractable text found.]")
}

/// Convert an Office/EPUB ZIP container to plain text (markitdown-equivalent).
///
/// Returns `None` when the file can't be opened as a ZIP or yields no text
/// (callers degrade to a friendly banner). This is the in-file analogue of
/// `markitdown_runtime.convert_to_markdown`; it pulls the human-readable text out
/// of the document's XML/text members rather than reproducing markitdown's full
/// Markdown structure (an HONEST simplification — the goal is model-legible text,
/// and the inline body is plain text either way).
fn convert_office_to_markdown(path: &str) -> Option<String> {
    let file = std::fs::File::open(path).ok()?;
    let mut archive = zip::ZipArchive::new(file).ok()?;

    let lower = path.to_lowercase();
    let ext = splitext_ext(&lower);

    // Which member names carry the body text for each container shape.
    // (substring match on the in-zip path, lowercased.)
    let wanted: &[&str] = match ext.as_str() {
        ".docx" => &["word/document.xml"],
        ".pptx" => &["ppt/slides/slide"],
        ".xlsx" => &["xl/sharedstrings.xml", "xl/worksheets/sheet"],
        ".epub" => &[".xhtml", ".html", ".htm"],
        _ => &[],
    };

    let mut collected = String::new();
    use std::io::Read as _;
    for i in 0..archive.len() {
        let mut entry = match archive.by_index(i) {
            Ok(e) => e,
            Err(_) => continue,
        };
        if !entry.is_file() {
            continue;
        }
        let name = entry.name().to_lowercase();
        let matches = if wanted.is_empty() {
            // .xls (legacy binary) isn't a ZIP — handled by the open failing
            // above; this arm never fires for it. Kept for completeness.
            false
        } else {
            wanted.iter().any(|w| name.contains(w))
        };
        if !matches {
            continue;
        }
        let mut buf = String::new();
        if entry.read_to_string(&mut buf).is_ok() {
            let text = strip_xml_tags(&buf);
            if !text.trim().is_empty() {
                if !collected.is_empty() {
                    collected.push('\n');
                }
                collected.push_str(text.trim());
            }
        }
    }

    if collected.trim().is_empty() {
        None
    } else {
        Some(collected)
    }
}

/// Strip XML/HTML tags from a document member, collapsing block boundaries to
/// newlines so the extracted prose stays readable. Decodes the handful of common
/// XML entities (`&amp; &lt; &gt; &quot; &apos;`).
fn strip_xml_tags(xml: &str) -> String {
    let mut out = String::with_capacity(xml.len());
    let mut in_tag = false;
    let mut last_tag = String::new();
    let mut chars = xml.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '<' => {
                in_tag = true;
                last_tag.clear();
            }
            '>' => {
                in_tag = false;
                // Paragraph / row / break / slide boundaries -> a newline so the
                // text doesn't run together (docx `</w:p>`, xlsx `</row>`, etc.).
                let t = last_tag.trim_start_matches('/');
                if t.ends_with("w:p")
                    || t.ends_with("w:br")
                    || t.ends_with("a:p")
                    || t.ends_with("/row")
                    || last_tag.starts_with("/w:p")
                    || last_tag.starts_with("/a:p")
                    || last_tag.starts_with("/row")
                    || last_tag.starts_with("br")
                    || last_tag.starts_with("p ")
                    || last_tag == "p"
                    || last_tag == "/p"
                    || last_tag == "div"
                    || last_tag == "/div"
                {
                    out.push('\n');
                }
            }
            _ if in_tag => last_tag.push(c),
            '&' => {
                // Decode a small set of XML entities.
                let mut entity = String::new();
                while let Some(&n) = chars.peek() {
                    if n == ';' {
                        chars.next();
                        break;
                    }
                    if entity.len() > 8 {
                        break;
                    }
                    entity.push(n);
                    chars.next();
                }
                match entity.as_str() {
                    "amp" => out.push('&'),
                    "lt" => out.push('<'),
                    "gt" => out.push('>'),
                    "quot" => out.push('"'),
                    "apos" => out.push('\''),
                    other if other.starts_with('#') => {
                        // Numeric entity (&#NN; / &#xNN;).
                        let code = if let Some(hex) = other.strip_prefix("#x").or_else(|| other.strip_prefix("#X")) {
                            u32::from_str_radix(hex, 16).ok()
                        } else {
                            other[1..].parse::<u32>().ok()
                        };
                        if let Some(ch) = code.and_then(char::from_u32) {
                            out.push(ch);
                        }
                    }
                    _ => {
                        // Unknown entity: keep it verbatim so nothing is lost.
                        out.push('&');
                        out.push_str(&entity);
                        out.push(';');
                    }
                }
            }
            _ => out.push(c),
        }
    }
    out
}

// ---------------------------------------------------------------------------
// strip_pdf_content_marker  (src/document_processor.py L231-L244)
// ---------------------------------------------------------------------------

/// Marker that `_process_pdf` prepends to extracted text.
const PDF_CONTENT_MARKER: &str = "\n\n[PDF content]:";

/// Remove the leading `[PDF content]:` wrapper that `_process_pdf` adds.
///
/// Uses `removeprefix` (a PREFIX strip), NOT `lstrip(chars)`: `lstrip` treats its
/// argument as a *set of characters*, so `lstrip("\n[PDF content]:")` keeps
/// chewing into the page text that follows the marker (e.g. it would eat a
/// leading "to" because 't' and 'o' are in the marker's char set). The faithful
/// behaviour is to strip the marker only when it is an exact leading prefix, then
/// `.strip()` the remainder.
pub fn strip_pdf_content_marker(text: &str) -> String {
    text.strip_prefix(PDF_CONTENT_MARKER)
        .unwrap_or(text)
        .trim()
        .to_string()
}

// ---------------------------------------------------------------------------
// _load_vl_settings  (src/document_processor.py L155-L161)
// ---------------------------------------------------------------------------

/// Load admin settings from disk.
///
/// Faithful port: Python imports `src.settings.load_settings()` and returns its
/// dict, falling back to `{}` on any failure. The Rust `settings::load_settings`
/// always returns a complete (defaults-merged) map and cannot raise, so the
/// `except Exception: return {}` arm is unreachable here — the observable shape
/// (a settings dict) is identical.
pub(crate) fn _load_vl_settings() -> Map<String, Value> {
    crate::src::settings::load_settings()
}

// ---------------------------------------------------------------------------
// _resolve_vl_model  (src/document_processor.py L164-L188)
// ---------------------------------------------------------------------------

/// Resolve the vision model to `(url, model_id, headers)`.
///
/// Uses the admin-configured model when `configured` is non-empty, otherwise
/// tries auto-detection of known vision-capable models across configured
/// endpoints in the EXACT Python priority order. Built on top of the
/// already-ported `ai_interaction::resolve_model_spec` (the port of the Python
/// `_resolve_model`) — no second endpoint/DB port.
///
/// Mirrors `src/document_processor.py:164-188`:
///   * `if configured:` -> `_resolve_model(configured)`.
///   * else iterate the candidate list, returning the FIRST that resolves
///     (Python `except (ValueError, Exception): continue` swallows ANY error per
///     candidate — replicated with `Err(_) => continue`).
///   * if none resolve -> `raise ValueError("No vision model available")`
///     (`PyError::value`).
///
/// `async` because `resolve_model_spec` probes endpoints over HTTP (the
/// OpenAI-compatible `/models` call) and is itself async.
pub(crate) async fn resolve_vl_model(
    configured: &str,
) -> crate::error::PyResult<(String, String, indexmap::IndexMap<String, String>)> {
    use crate::src::ai_interaction::resolve_model_spec;

    // `if configured:` — Python truthiness: a non-empty string.
    if !configured.is_empty() {
        return resolve_model_spec(configured, None).await;
    }

    // Auto-detect: try known vision-capable models in priority order. List copied
    // VERBATIM from document_processor.py:176-180.
    const CANDIDATES: &[&str] = &[
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4.1",
        "gpt-4.1-mini",
        "claude-sonnet-4-5-20250929",
        "claude-opus-4-20250514",
        "gemini-2.0-flash",
        "gemini-2.5-pro",
        "llava",
        "pixtral",
        "qwen2-vl",
    ];
    for candidate in CANDIDATES {
        match resolve_model_spec(candidate, None).await {
            Ok(t) => return Ok(t),
            // Python `except (ValueError, Exception): continue` — swallow ANY error.
            Err(_) => continue,
        }
    }

    Err(crate::error::PyError::value("No vision model available"))
}

// ---------------------------------------------------------------------------
// vl_chat — shared single-image vision message builder
// ---------------------------------------------------------------------------

/// SHARED vision call: build the OpenAI-shaped `[text, image_url]` user message
/// (plus an optional leading `system` message) and dispatch it through
/// `llm_core::llm_call_async`.
///
/// This is the ONE place the single-image vision content block is assembled. It
/// mirrors how Python's vision consumers build their request: an OpenAI-style
/// content list with a `{"type": "text"}` part and a `{"type": "image_url",
/// "image_url": {"url": <data-uri>}}` part (see
/// `src/document_processor.py:212-220` and `routes/gallery_routes.py:1693-1700`).
/// The caller pre-builds the `data:image/<fmt>;base64,<b64>` `data_uri` so each
/// caller keeps its own exact mime/format logic (gallery uses a full
/// `image/<x>` mime, doc-OCR uses a bare `<fmt>`).
///
/// Provider routing is NOT done here. `llm_call_async` decides OpenAI vs
/// Anthropic internally via `_detect_provider(url)` (an Anthropic `url` ends in
/// `/v1/messages`, exactly how the Python `llm_call` path decides) and converts
/// the OpenAI content list into the Anthropic image-source block shape. Callers
/// therefore NEVER branch on provider for the message shape — identical to
/// Python, where doc-OCR / ai-fill pass the OpenAI shape and let `llm_call`
/// convert.
///
/// Returns the assistant reply text (the model's description / tags / etc.).
#[allow(clippy::too_many_arguments)]
pub(crate) async fn vl_chat(
    url: &str,
    model: &str,
    headers: indexmap::IndexMap<String, String>,
    data_uri: &str,
    prompt: &str,
    system: Option<&str>,
    temperature: f64,
    max_tokens: i64,
    timeout: u64,
) -> crate::error::PyResult<String> {
    let mut messages: Vec<Value> = Vec::new();
    // Optional leading system message (`{"role": "system", "content": <system>}`).
    if let Some(sys) = system {
        messages.push(json!({"role": "system", "content": sys}));
    }
    // The user turn: a content list with the text part FIRST then the image part,
    // matching the Python `vl_messages` order (text block, then image_url block).
    messages.push(json!({
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ],
    }));

    crate::src::llm_core::llm_call_async(
        url, model, messages, temperature, max_tokens, headers, timeout,
    )
    .await
}

// ---------------------------------------------------------------------------
// analyze_image_with_vl_result / analyze_image_with_vl
//   (src/document_processor.py L191-L250)
// ---------------------------------------------------------------------------

/// Analyze an image and return both text and the model that produced it.
///
/// Faithful port of `src/document_processor.py:191-245`. The public function is
/// SYNC (its signature is unchanged, so the `chat_handler` / `upload_routes`
/// callers are untouched), but the work it does — `_resolve_vl_model` and the
/// `llm_call_async` round-trip — is async. We bridge the async inner via
/// `block_in_place` + `Handle::block_on` when already inside the tokio runtime
/// (the live callers run inside the `#[tokio::main]` multi-threaded runtime),
/// falling back to a fresh current-thread runtime for sync callers / tests —
/// the documented `app_initializer`/`event_bus` async->sync precedent.
///
/// Soft-fail shapes match Python EXACTLY (always `{"text": ..., "model": ...}`,
/// never a raise, never a fabricated description):
///   * vision disabled    -> `{text: "[Vision is disabled — enable it in Settings → Vision]", model: ""}`
///   * no model resolves   -> `{text: "[No vision model configured — set one in Settings → Vision]", model: <configured>}`
///   * all candidates fail -> `{text: "[VL model unavailable - image not analyzed]", model: ""}`
pub fn analyze_image_with_vl_result(image_path: &str) -> Map<String, Value> {
    logger::info(&format!("Analyzing image with VL model: {image_path}"));
    let image_path = image_path.to_string();
    let fut = async move { analyze_image_with_vl_result_async(&image_path).await };

    // Drive the async work to completion from this sync context.
    match tokio::runtime::Handle::try_current() {
        // Inside the (multi-threaded `#[tokio::main]`) runtime: hand off to a
        // blocking section so we don't park a worker, then block on the future.
        Ok(handle) => tokio::task::block_in_place(|| handle.block_on(fut)),
        // No running runtime (tests / sync callers): spin up a current-thread one.
        Err(_) => match tokio::runtime::Builder::new_current_thread().enable_all().build() {
            Ok(rt) => rt.block_on(fut),
            Err(e) => {
                // Building a runtime should never fail; degrade to the honest
                // unavailable marker rather than panic (Python's broad
                // `except Exception` returns the same marker).
                logger::error(&format!(
                    "VL model unavailable: failed to build runtime: {e}"
                ));
                _vl_result("[VL model unavailable - image not analyzed]", "")
            }
        },
    }
}

/// Build the `{"text": ..., "model": ...}` result map (the Python return shape).
fn _vl_result(text: &str, model: &str) -> Map<String, Value> {
    let mut out = Map::new();
    out.insert("text".to_string(), Value::String(text.to_string()));
    out.insert("model".to_string(), Value::String(model.to_string()));
    out
}

/// Async core of `analyze_image_with_vl_result`. Port of
/// `analyze_image_with_vl_result` (`src/document_processor.py:191-245`).
async fn analyze_image_with_vl_result_async(image_path: &str) -> Map<String, Value> {
    // `settings = _load_vl_settings()`
    let settings = _load_vl_settings();
    // `if not settings.get("vision_enabled", True):`  (Python default True)
    let vision_enabled = settings
        .get("vision_enabled")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    if !vision_enabled {
        return _vl_result("[Vision is disabled — enable it in Settings → Vision]", "");
    }
    // `vl_model = settings.get("vision_model", "")`
    let vl_model = settings
        .get("vision_model")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();

    // `try: url, model_id, headers = _resolve_vl_model(vl_model)
    //  except ValueError: return {"text": "[No vision model configured …]", "model": vl_model or ""}`
    //
    // The Rust `resolve_vl_model` surfaces ValueError as `PyError::value(...)`.
    // Every error it can raise IS a `ValueError` (`"No vision model available"`
    // from the auto path / `"No enabled endpoints found"` etc. from
    // `resolve_model_spec`), so treating the resolver `Err` arm as the no-model
    // path matches Python's `except ValueError`.
    let (url, model_id, headers) = match resolve_vl_model(&vl_model).await {
        Ok(t) => t,
        Err(_) => {
            return _vl_result(
                "[No vision model configured — set one in Settings → Vision]",
                &vl_model,
            );
        }
    };

    // `with open(image_path, "rb") as f: img_data = base64(f.read()).decode()`.
    // Any IO error here lands in Python's outer `except Exception` -> the
    // `[VL model unavailable …]` marker.
    let img_bytes = match std::fs::read(image_path) {
        Ok(b) => b,
        Err(e) => {
            logger::error(&format!("VL model unavailable: {e}"));
            return _vl_result("[VL model unavailable - image not analyzed]", "");
        }
    };
    let img_data = base64::engine::general_purpose::STANDARD.encode(&img_bytes);

    // `ext = os.path.splitext(image_path)[1].lower()`; mime_map ext -> fmt (default jpeg).
    let ext = splitext_ext(image_path).to_lowercase();
    let img_format = match ext.as_str() {
        ".jpg" | ".jpeg" => "jpeg",
        ".png" => "png",
        ".gif" => "gif",
        ".webp" => "webp",
        _ => "jpeg",
    };
    // doc-OCR data-uri keeps Python's exact string: `data:image/<fmt>;base64,<b64>`.
    let data_uri = format!("data:image/{img_format};base64,{img_data}");

    // Vision-specific fallback chain: `[(url, model_id, headers)] +
    // resolve_vision_fallback_candidates()` (Python L224-L228). The Rust resolver
    // is infallible (no `except`), so the candidate list is always the primary
    // plus the configured vision fallbacks.
    let mut candidates: Vec<(String, String, indexmap::IndexMap<String, String>)> =
        vec![(url, model_id, headers)];
    candidates.extend(crate::src::endpoint_resolver::resolve_vision_fallback_candidates());

    // `for i, (_url, _model, _headers) in enumerate([c for c in _vl_candidates if c and c[0] and c[1]]):`
    let mut last_err: Option<crate::error::PyError> = None;
    let mut i = 0usize;
    for (cand_url, cand_model, cand_headers) in candidates
        .into_iter()
        .filter(|(u, m, _)| !u.is_empty() && !m.is_empty())
    {
        // `description = llm_call(_url, _model, vl_messages, headers=_headers, timeout=120)`.
        // Python passes no temperature/max_tokens => DEFAULT_TEMPERATURE (1.0) /
        // DEFAULT_MAX_TOKENS (0); timeout=120 (bumped from 30s — slower vision
        // endpoints were timing out). The "Describe this image in detail" prompt
        // is verbatim from L308.
        match vl_chat(
            &cand_url,
            &cand_model,
            cand_headers,
            &data_uri,
            "Describe this image in detail",
            None,
            crate::src::llm_core::LLMConfig::DEFAULT_TEMPERATURE,
            crate::src::llm_core::LLMConfig::DEFAULT_MAX_TOKENS,
            120,
        )
        .await
        {
            Ok(description) => {
                logger::info(&format!("VL analysis complete with model {cand_model}"));
                return _vl_result(&description, &cand_model);
            }
            Err(e) => {
                let tag = if i == 0 { "primary" } else { "candidate" };
                logger::warning(&format!(
                    "[vision fallback] {tag} {cand_model} failed; trying next"
                ));
                last_err = Some(e);
                i += 1;
                continue;
            }
        }
    }

    // `raise last_err if last_err else RuntimeError("No vision model endpoint configured")`
    // — both land in the outer `except Exception` -> the unavailable marker.
    if let Some(e) = last_err {
        logger::error(&format!("VL model unavailable: {e}"));
    } else {
        logger::error("VL model unavailable: No vision model endpoint configured");
    }
    _vl_result("[VL model unavailable - image not analyzed]", "")
}

/// Analyze an image using the admin-configured Vision-Language model.
///
/// Faithful port — delegates to `analyze_image_with_vl_result` and returns its
/// `text`, exactly like the Python `.get("text", "")`.
pub fn analyze_image_with_vl(image_path: &str) -> String {
    analyze_image_with_vl_result(image_path)
        .get("text")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

// ---------------------------------------------------------------------------
// build_user_content  (src/document_processor.py L345-L548)
// ---------------------------------------------------------------------------

/// Build user content with attachments (text, images, audio, documents).
///
/// PORT_PARTIAL. Returns Python's `str | list[dict]` as a `serde_json::Value`:
///   * `Value::String` when there is no inlined media (text-only collapse path).
///   * `Value::Array` of content blocks otherwise.
///
/// Attachments are resolved OWNER-AWARE: the optional `resolved_uploads` map is
/// consulted first (pre-resolved by the caller), then
/// `upload_handler.resolve_upload(fid, owner)`. The on-disk path / mime / display
/// name come from the resolved metadata, NOT from re-joining `upload_dir` (the
/// old global/walkdir resolution is gone). `_upload_dir` is retained in the
/// signature for caller parity but is no longer read here.
///
/// Inline document text shares a `MAX_INLINE_ATTACHMENT_CHARS` budget across the
/// whole call via `_fit_inline_attachment_text`, so a multi-file batch can't blow
/// the model's context; Office/EPUB documents go through the markitdown-
/// equivalent `_process_office_document`.
///
/// If `session_id` is provided and an attached PDF contains AcroForm fields, a
/// markdown Document is auto-created so the user can edit the form in the editor.
/// When `auto_opened_docs` is supplied, an entry is appended for each such doc so
/// the chat route can emit a `doc_update` SSE event.
///
/// The only DEFERRED transitive dependency is the VL OCR inside `_process_pdf`
/// (image-heavy pages); everything else here is a faithful port.
#[allow(clippy::too_many_arguments)]
pub fn build_user_content<U: UploadHandlerLike>(
    text: &str,
    attachment_ids: &[String],
    _upload_dir: &str,
    upload_handler: &U,
    session_id: Option<&str>,
    auto_opened_docs: Option<&mut Vec<Value>>,
    owner: Option<&str>,
    resolved_uploads: &Map<String, Value>,
) -> Value {
    // content = [{"type": "text", "text": text}]
    let mut content: Vec<Value> = vec![json!({"type": "text", "text": text})];

    // inline_attachment_remaining = MAX_INLINE_ATTACHMENT_CHARS
    let mut inline_attachment_remaining = MAX_INLINE_ATTACHMENT_CHARS;

    // `auto_opened_docs is not None` in Python is a sentinel; carry the Option
    // through and only push when Some.
    let mut auto_opened_docs = auto_opened_docs;

    for fid in attachment_ids {
        // upload_info = (resolved_uploads or {}).get(fid)
        // if upload_info is None and hasattr(upload_handler, "resolve_upload"):
        //     upload_info = upload_handler.resolve_upload(fid, owner=owner)
        //
        // `resolved_uploads` holds JSON objects (the pre-resolved upload metadata,
        // built by the chat path's owner-checked `resolve_upload`); pull the row
        // for `fid` as a map, else fall back to the handler's owner-aware resolve.
        let mut upload_info = resolved_uploads
            .get(fid)
            .and_then(|v| v.as_object())
            .cloned();
        if upload_info.is_none() {
            upload_info = upload_handler.resolve_upload(fid, owner);
        }
        let upload_info = match upload_info {
            Some(i) => i,
            None => {
                // if upload_info is None: warn "not found or not authorized"; continue
                logger::warning(&format!("Attachment {fid} not found or not authorized"));
                continue;
            }
        };

        // path = upload_info.get("path")
        // if not path or not os.path.exists(path): warn; continue
        let path = match upload_info.get("path").and_then(|v| v.as_str()) {
            Some(p) if !p.is_empty() && crate::pyos::path::exists(p) => p.to_string(),
            _ => {
                logger::warning(&format!("Attachment {fid} path is missing"));
                continue;
            }
        };

        // The real handler exposes `_inside_upload_dir`; fall back to
        // `inside_base_dir` exactly like the Python hasattr branch.
        if !upload_handler.inside_upload_dir(&path) {
            logger::warning(&format!(
                "Attachment {fid} path is outside upload directory: {path}"
            ));
            continue;
        }

        // _, ext = os.path.splitext(path.lower())
        let ext = splitext_ext(&path.to_lowercase());
        // mime = upload_info.get("mime") or mimetypes.guess_type(path)[0] or "application/octet-stream"
        let mime = upload_info
            .get("mime")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string())
            .or_else(|| {
                mime_guess::from_path(&path)
                    .first_raw()
                    .map(|s| s.to_string())
            })
            .unwrap_or_else(|| "application/octet-stream".to_string());

        // display_name = upload_info.get("name") or upload_info.get("original_name") or path
        let display_name = upload_info
            .get("name")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .or_else(|| {
                upload_info
                    .get("original_name")
                    .and_then(|v| v.as_str())
                    .filter(|s| !s.is_empty())
            })
            .map(|s| s.to_string())
            .unwrap_or_else(|| path.clone());

        if upload_handler.is_image_file(&display_name, &mime) {
            match std::fs::read(&path) {
                Ok(bytes) => {
                    let encoded = base64::engine::general_purpose::STANDARD.encode(&bytes);
                    // image_format = ext[1:]
                    let image_format = ext.strip_prefix('.').unwrap_or("");
                    content.push(json!({
                        "type": "image_url",
                        "image_url": {"url": format!("data:image/{image_format};base64,{encoded}")},
                    }));
                }
                Err(e) => {
                    logger::error(&format!("Failed to encode image {fid}: {e}"));
                    append_or_insert_text(
                        &mut content,
                        "\n\n[Image attached but could not be processed]",
                        "[Image attached but could not be processed]",
                    );
                }
            }
        } else if upload_handler.is_audio_file(&display_name, &mime) {
            match std::fs::read(&path) {
                Ok(bytes) => {
                    let encoded = base64::engine::general_purpose::STANDARD.encode(&bytes);
                    let audio_format = ext.strip_prefix('.').unwrap_or("");
                    content.push(json!({
                        "type": "audio",
                        "audio": {"url": format!("data:audio/{audio_format};base64,{encoded}")},
                    }));
                }
                Err(e) => {
                    logger::error(&format!("Failed to encode audio {fid}: {e}"));
                    append_or_insert_text(
                        &mut content,
                        "\n\n[Audio attached but could not be processed]",
                        "[Audio attached but could not be processed]",
                    );
                }
            }
        } else if upload_handler.is_document_file(&display_name, &mime) {
            let extracted_text: String = if mime == "application/pdf" {
                let mut extracted: Option<String> = None;
                if let Some(sid) = session_id {
                    extracted = pdf_form_branch(sid, &path, auto_opened_docs.as_deref_mut());
                }
                // `if extracted_text is None: extracted_text = _process_pdf(path)`
                extracted.unwrap_or_else(|| _process_pdf(&path))
            } else if mime.starts_with("text/") || _is_text_file(&path) {
                _process_text_file(&path)
            } else {
                // Office/EPUB -> markitdown-equivalent extractor.
                process_office_document(&path, &display_name)
            };

            // extracted_text, inline_attachment_remaining = _fit_inline_attachment_text(
            //     extracted_text, inline_attachment_remaining, display_name)
            let (extracted_text, new_remaining) = fit_inline_attachment_text(
                &extracted_text,
                inline_attachment_remaining,
                &display_name,
            );
            inline_attachment_remaining = new_remaining;

            // if content and content[0]["type"] == "text": append; else insert.
            if first_is_text(&content) {
                append_text(&mut content[0], &extracted_text);
            } else {
                content.insert(
                    0,
                    json!({"type": "text", "text": extracted_text.trim_start().to_string()}),
                );
            }
        } else {
            append_or_insert_text(
                &mut content,
                "\n\n[Attached non-text file]",
                "[Attached non-text file]",
            );
        }
    }

    // has_media = any(item type in [image_url, audio]).
    let has_media = content.iter().any(|item| {
        matches!(
            item.get("type").and_then(|t| t.as_str()),
            Some("image_url") | Some("audio")
        )
    });
    if !has_media && !content.is_empty() {
        let mut combined = String::new();
        for item in &content {
            if item.get("type").and_then(|t| t.as_str()) == Some("text") {
                combined.push_str(item.get("text").and_then(|t| t.as_str()).unwrap_or(""));
            }
        }
        return Value::String(combined.trim().to_string());
    }

    Value::Array(content)
}

// ---------------------------------------------------------------------------
// PDF auto-doc branch (build_user_content L330-L427)
// ---------------------------------------------------------------------------

/// The `if mime == "application/pdf"` + `if session_id:` block, factored out for
/// readability. Returns `Some(extracted_text)` when a doc was created (so the
/// caller does NOT fall through to `_process_pdf`), or `None` to fall through.
///
/// Mirrors the Python try/except: any failure logs a warning and returns `None`
/// (`extracted_text` stays `None` -> caller runs `_process_pdf`).
fn pdf_form_branch(
    session_id: &str,
    path: &str,
    auto_opened_docs: Option<&mut Vec<Value>>,
) -> Option<String> {
    use crate::src::pdf_form_doc::{
        create_form_markdown_document, create_plain_pdf_document, save_field_sidecar,
    };
    use crate::src::pdf_forms::{extract_fields, has_form_fields};

    // title = os.path.splitext(os.path.basename(path))[0]
    let basename = crate::pyos::path::basename(path);
    let title = splitext_root(&basename);

    // pdf_body_text = strip_pdf_content_marker(_process_pdf(path))
    //
    // PORT NOTE: the upstream now uses `str.removeprefix("\n\n[PDF content]:")`
    // (a PREFIX strip), NOT the old `str.lstrip("\n[PDF content]:")` char-set
    // strip. `lstrip` treats its argument as a *set of characters* and would
    // chew into the page text that follows the marker (e.g. eat a leading "to"
    // because 't'/'o' are in the marker's char set). `strip_pdf_content_marker`
    // strips the marker only as an exact leading prefix, then `.strip()`s.
    let pdf_body_text = strip_pdf_content_marker(&_process_pdf(path));

    // is_form = has_form_fields(path) (guarded).
    let is_form = std::panic::catch_unwind(|| has_form_fields(path)).unwrap_or_else(|_| {
        logger::warning(&format!("PDF form detection failed for {path}"));
        false
    });

    // Inline copy cap (_MAX_INLINE_CHARS = 15000), char-based.
    const MAX_INLINE_CHARS: usize = 15000;
    let body_chars: Vec<char> = pdf_body_text.trim().chars().collect();
    let mut body_for_chat: String = body_chars.iter().collect();
    let mut truncated_marker = String::new();
    if !body_for_chat.is_empty() && body_chars.len() > MAX_INLINE_CHARS {
        body_for_chat = body_chars[..MAX_INLINE_CHARS].iter().collect();
        truncated_marker =
            "\n[…truncated for inline context — full text available in the document viewer.]"
                .to_string();
    }

    // upload_id = os.path.basename(path)
    let upload_id = crate::pyos::path::basename(path);

    let mut extracted_text: Option<String> = None;
    let mut created_doc_id: Option<String> = None;

    if is_form {
        let fields = extract_fields(path);
        save_field_sidecar(path, &fields);
        let intro = if pdf_body_text.is_empty() {
            None
        } else {
            Some(pdf_body_text.as_str())
        };
        let doc_id =
            create_form_markdown_document(session_id, &fields, &upload_id, &title, intro);
        if let Some(doc_id) = doc_id {
            let mut txt = format!(
                "\n\n[Form attached: {title} — {} fields. Opened in editor — edit \
                 the values there and use the Export PDF button when done.]",
                fields.len()
            );
            if !body_for_chat.is_empty() {
                txt.push_str(&format!(
                    "\n\n[PDF content — {title}]:\n{body_for_chat}{truncated_marker}"
                ));
            }
            extracted_text = Some(txt);
            created_doc_id = Some(doc_id);
        }
    } else {
        let body = if pdf_body_text.is_empty() {
            None
        } else {
            Some(pdf_body_text.as_str())
        };
        let doc_id = create_plain_pdf_document(session_id, &upload_id, &title, body);
        if let Some(doc_id) = doc_id {
            let mut txt = format!("\n\n[PDF attached: {title} — opened in document viewer.]");
            if !body_for_chat.is_empty() {
                txt.push_str(&format!(
                    "\n\n[PDF content — {title}]:\n{body_for_chat}{truncated_marker}"
                ));
            }
            extracted_text = Some(txt);
            created_doc_id = Some(doc_id);
        }
    }

    // if doc_id and auto_opened_docs is not None: SELECT the row + append.
    if let (Some(doc_id), Some(docs)) = (created_doc_id.as_deref(), auto_opened_docs) {
        if let Some(entry) = load_auto_opened_doc(doc_id) {
            docs.push(entry);
        }
    }

    extracted_text
}

/// SELECT the freshly-created document row for the `auto_opened_docs` payload.
///
/// Mirrors Python L410-L425: `db.query(Document).filter(Document.id == doc_id)`
/// then build `{doc_id, title, language, content, version}` from
/// `id / title / language / current_content / version_count`. Returns `None` if
/// the row is missing or the DB read fails (Python: the `if _d:` guard + the
/// outer try/except in the caller).
fn load_auto_opened_doc(doc_id: &str) -> Option<Value> {
    let conn = crate::core::database::session_local().ok()?;
    conn.query_row(
        "SELECT id, title, language, current_content, version_count \
         FROM documents WHERE id = ?1",
        [doc_id],
        |r| {
            let id: String = r.get(0)?;
            let title: String = r.get(1)?;
            let language: Option<String> = r.get(2)?;
            let current_content: String = r.get(3)?;
            let version_count: Option<i64> = r.get(4)?;
            Ok(json!({
                "doc_id": id,
                "title": title,
                "language": language,
                "content": current_content,
                "version": version_count,
            }))
        },
    )
    .ok()
}

// ---------------------------------------------------------------------------
// Small string/content helpers (faithful to the Python idioms)
// ---------------------------------------------------------------------------

/// `content and content[0]["type"] == "text"`.
fn first_is_text(content: &[Value]) -> bool {
    content
        .first()
        .and_then(|c| c.get("type"))
        .and_then(|t| t.as_str())
        == Some("text")
}

/// `content[0]["text"] += suffix`.
fn append_text(item: &mut Value, suffix: &str) {
    if let Some(obj) = item.as_object_mut() {
        let cur = obj
            .get("text")
            .and_then(|t| t.as_str())
            .unwrap_or("")
            .to_string();
        obj.insert("text".to_string(), Value::String(cur + suffix));
    }
}

/// The repeated `if content and content[0]["type"] == "text": += ; else insert`
/// idiom used for the image/audio/non-text error markers.
fn append_or_insert_text(content: &mut Vec<Value>, append_suffix: &str, insert_text: &str) {
    if first_is_text(content) {
        append_text(&mut content[0], append_suffix);
    } else {
        content.insert(0, json!({"type": "text", "text": insert_text}));
    }
}

/// `os.path.splitext(p)[1]` — the extension *including* the leading dot, or "".
///
/// Faithful to CPython `splitext`: a leading-dot basename (e.g. ".bashrc") has no
/// extension, and only the final dot in the basename counts.
fn splitext_ext(p: &str) -> String {
    let base = crate::pyos::path::basename(p);
    match base.rfind('.') {
        // Leading dot(s) only (e.g. ".bashrc", "..") -> no extension.
        Some(idx) if base[..idx].chars().all(|c| c == '.') => String::new(),
        Some(idx) => base[idx..].to_string(),
        None => String::new(),
    }
}

/// `os.path.splitext(p)[0]` for a basename — the root (filename minus extension).
fn splitext_root(base: &str) -> String {
    match base.rfind('.') {
        Some(idx) if base[..idx].chars().all(|c| c == '.') => base.to_string(),
        Some(idx) => base[..idx].to_string(),
        None => base.to_string(),
    }
}

/// `f"{n:,}"` — group an unsigned integer with comma thousands separators.
fn group_thousands(n: u64) -> String {
    let s = n.to_string();
    let bytes = s.as_bytes();
    let mut out = String::with_capacity(s.len() + s.len() / 3);
    let len = bytes.len();
    for (i, b) in bytes.iter().enumerate() {
        if i > 0 && (len - i).is_multiple_of(3) {
            out.push(',');
        }
        out.push(*b as char);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn is_text_file_matches_extensions() {
        assert!(_is_text_file("/x/a.py"));
        assert!(_is_text_file("/x/A.MD"));
        assert!(_is_text_file("notes.log"));
        assert!(!_is_text_file("/x/a.pdf"));
        assert!(!_is_text_file("/x/a.png"));
    }

    #[test]
    fn splitext_ext_parity() {
        assert_eq!(splitext_ext("/a/b/file.txt"), ".txt");
        assert_eq!(splitext_ext("/a/b/file"), "");
        assert_eq!(splitext_ext("/a/b/.bashrc"), "");
        assert_eq!(splitext_ext("/a/b/archive.tar.gz"), ".gz");
        assert_eq!(splitext_root("file.txt"), "file");
        assert_eq!(splitext_root(".bashrc"), ".bashrc");
        assert_eq!(splitext_root("archive.tar.gz"), "archive.tar");
    }

    #[test]
    fn group_thousands_parity() {
        assert_eq!(group_thousands(0), "0");
        assert_eq!(group_thousands(999), "999");
        assert_eq!(group_thousands(1000), "1,000");
        assert_eq!(group_thousands(1234567), "1,234,567");
    }

    #[test]
    fn vision_returns_honest_marker_no_endpoints() {
        // With the default settings (vision_enabled=true, vision_model="") and a
        // test DB that has no enabled endpoints, `resolve_vl_model("")` exhausts
        // the auto-detect candidate list and raises ValueError, OR a model resolves
        // but the (nonexistent) image fails to read — either way the port returns
        // one of the HONEST bracketed markers and NEVER a fabricated description.
        // The exact marker depends on the test environment's endpoint table, so we
        // assert the invariant rather than a single string.
        let honest = [
            "[Vision is disabled — enable it in Settings → Vision]",
            "[No vision model configured — set one in Settings → Vision]",
            "[VL model unavailable - image not analyzed]",
        ];
        let r = analyze_image_with_vl_result("/tmp/odysseus-nonexistent-vision-test.png");
        let text = r.get("text").and_then(|v| v.as_str()).unwrap_or("");
        assert!(
            honest.contains(&text),
            "expected an honest vision marker, got: {text:?}"
        );
        // `analyze_image_with_vl` returns the same `text`.
        let t2 = analyze_image_with_vl("/tmp/odysseus-nonexistent-vision-test.png");
        assert!(
            honest.contains(&t2.as_str()),
            "expected an honest vision marker, got: {t2:?}"
        );
    }

    #[test]
    fn load_vl_settings_returns_complete_map() {
        // `settings::load_settings` always returns a defaults-merged map and never
        // raises, so `_load_vl_settings` mirrors it without the `except: {}` arm.
        // We only assert it produced a (non-empty) settings dict — its exact keys
        // are owned by the settings port, not this module.
        let s = _load_vl_settings();
        assert!(
            !s.is_empty(),
            "_load_vl_settings should return the defaults-merged settings map"
        );
    }

    // `resolve_vl_model` auto-detect path: with NO enabled endpoints in the DB,
    // every candidate's `resolve_model_spec` errors ("No enabled endpoints found")
    // and is swallowed, so the empty-`configured` call must end in the Python
    // `ValueError("No vision model available")`. Uses an ISOLATED empty temp DB
    // (mirrors `ai_interaction::resolve_model_spec_tests` — `DATABASE_URL` is
    // process-global, never the live data dir).
    //
    // `#[ignore]`d by default: `DATABASE_URL` is process-global and unsynchronized
    // across the lib-test binary, so running this CONCURRENTLY with
    // `ai_interaction::resolve_model_spec_tests` (which seeds an enabled endpoint
    // into that same global DB) is a known race — a seeded endpoint can satisfy a
    // candidate and flip the expected error. Run on demand, serially, with:
    //   `cargo test --lib -- --ignored --test-threads=1 \
    //        resolve_vl_model_auto_with_no_endpoints_errs`
    // The always-on `load_vl_settings_returns_complete_map` covers the race-free
    // surface owned by this module.
    #[tokio::test]
    #[ignore = "mutates process-global DATABASE_URL; races other DB tests in the shared lib-test binary — run with --ignored --test-threads=1"]
    async fn resolve_vl_model_auto_with_no_endpoints_errs() {
        let dir = std::env::temp_dir();
        let unique = format!(
            "odysseus_resolve_vl_{}_{}.db",
            std::process::id(),
            uuid::Uuid::new_v4().simple()
        );
        let path = dir.join(unique);
        struct TmpDb(std::path::PathBuf);
        impl Drop for TmpDb {
            fn drop(&mut self) {
                let _ = std::fs::remove_file(&self.0);
            }
        }
        let _guard = TmpDb(path.clone());
        let _ = std::fs::remove_file(&path);
        std::env::set_var("DATABASE_URL", format!("sqlite:///{}", path.display()));
        crate::core::database::create_all().unwrap();

        // configured == "" -> auto-detect; no enabled endpoints -> all candidates
        // fail -> the final ValueError, byte-faithful to the Python message.
        let err = resolve_vl_model("").await.unwrap_err();
        assert_eq!(err.to_string(), "No vision model available");
    }

    // A minimal UploadHandlerLike for build_user_content tests: treats every
    // path as inside the base/upload dir, validates any non-empty id, and
    // classifies by extension/mime the way the real handler's predicates do for
    // these inputs. `resolve_upload` joins `dir`/`fid` (the owner-aware resolver
    // surface the real handler now exposes), so the tests exercise the new
    // resolve-then-read path rather than the removed walkdir search.
    struct FakeHandler {
        dir: String,
    }
    impl FakeHandler {
        fn new(dir: &str) -> Self {
            FakeHandler {
                dir: dir.to_string(),
            }
        }
    }
    impl UploadHandlerLike for FakeHandler {
        fn validate_upload_id(&self, upload_id: &str) -> bool {
            !upload_id.is_empty()
        }
        fn inside_base_dir(&self, _path: &str) -> bool {
            true
        }
        fn is_image_file(&self, _f: &str, ct: &str) -> bool {
            ct.starts_with("image/")
        }
        fn is_audio_file(&self, _f: &str, ct: &str) -> bool {
            ct.starts_with("audio/")
        }
        fn is_document_file(&self, f: &str, ct: &str) -> bool {
            ct == "application/pdf"
                || ct.starts_with("text/")
                || _is_text_file(f)
                || ct == "application/octet-stream"
        }
        fn resolve_upload(&self, upload_id: &str, _owner: Option<&str>) -> Option<Map<String, Value>> {
            let path = crate::pyos::path::join(&self.dir, upload_id);
            if !crate::pyos::path::exists(&path) {
                return None;
            }
            let mut m = Map::new();
            m.insert("id".to_string(), Value::String(upload_id.to_string()));
            m.insert("path".to_string(), Value::String(path));
            m.insert("name".to_string(), Value::String(upload_id.to_string()));
            m
                .insert(
                    "mime".to_string(),
                    Value::String(
                        mime_guess::from_path(upload_id)
                            .first_raw()
                            .unwrap_or("application/octet-stream")
                            .to_string(),
                    ),
                );
            Some(m)
        }
    }

    #[test]
    fn build_user_content_text_only_collapses_to_string() {
        // No attachments, no media -> returns the trimmed combined text as a string.
        let out = build_user_content(
            "  hello world  ",
            &[],
            "/nonexistent",
            &FakeHandler::new("/nonexistent"),
            None,
            None,
            None,
            &Map::new(),
        );
        assert_eq!(out, Value::String("hello world".to_string()));
    }

    #[test]
    fn build_user_content_inlines_image_block() {
        let dir = std::env::temp_dir().join(format!("docproc_test_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let img = dir.join("pic.png");
        std::fs::write(&img, b"\x89PNG\r\n\x1a\nfake").unwrap();

        let out = build_user_content(
            "look",
            &["pic.png".to_string()],
            dir.to_str().unwrap(),
            &FakeHandler::new(dir.to_str().unwrap()),
            None,
            None,
            None,
            &Map::new(),
        );
        // Media present -> stays a list with a text block + an image_url block.
        let arr = out.as_array().expect("array");
        assert_eq!(arr[0]["type"], "text");
        assert_eq!(arr[0]["text"], "look");
        assert_eq!(arr[1]["type"], "image_url");
        let url = arr[1]["image_url"]["url"].as_str().unwrap();
        assert!(url.starts_with("data:image/png;base64,"));

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn build_user_content_appends_text_file_body() {
        let dir = std::env::temp_dir().join(format!("docproc_txt_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let f = dir.join("notes.txt");
        std::fs::write(&f, "alpha\nbeta").unwrap();

        let out = build_user_content(
            "see file",
            &["notes.txt".to_string()],
            dir.to_str().unwrap(),
            &FakeHandler::new(dir.to_str().unwrap()),
            None,
            None,
            None,
            &Map::new(),
        );
        // No media -> collapses to a string with the file header + body appended.
        let s = out.as_str().expect("string");
        assert!(s.starts_with("see file"));
        assert!(s.contains("=== File: notes.txt ==="));
        assert!(s.contains("[Type: text, Lines: 2"));
        assert!(s.contains("alpha\nbeta"));

        let _ = std::fs::remove_dir_all(&dir);
    }

    // -------------------------------------------------------------------
    // strip_pdf_content_marker — removeprefix, NOT lstrip(char-set).
    // -------------------------------------------------------------------
    #[test]
    fn strip_pdf_content_marker_uses_removeprefix_not_charset() {
        // The exact regression the Python docstring calls out: a body that begins
        // with "to" after the marker must keep its "to" — a char-set lstrip would
        // eat it because 't'/'o' are in "[PDF content]:".
        let input = "\n\n[PDF content]:\n\n[Page 1 text]:\nto the board";
        let out = strip_pdf_content_marker(input);
        assert_eq!(out, "[Page 1 text]:\nto the board");
        assert!(out.starts_with("[Page 1"));

        // No marker -> just `.strip()`.
        assert_eq!(strip_pdf_content_marker("  hello  "), "hello");
        // Marker not at the very start is left in place (prefix only).
        assert_eq!(
            strip_pdf_content_marker("x\n\n[PDF content]:y"),
            "x\n\n[PDF content]:y"
        );
    }

    // -------------------------------------------------------------------
    // truncate_inline — char-based cap + banner.
    // -------------------------------------------------------------------
    #[test]
    fn truncate_inline_caps_and_marks() {
        let (body, marker) = truncate_inline("  short  ", 15000);
        assert_eq!(body, "short");
        assert_eq!(marker, "");

        let big = "x".repeat(20);
        let (body, marker) = truncate_inline(&big, 5);
        assert_eq!(body, "xxxxx");
        assert_eq!(marker, "\n[…truncated for inline context.]");
    }

    // -------------------------------------------------------------------
    // fit_inline_attachment_text — shared budget: fit / slice / omit.
    // -------------------------------------------------------------------
    #[test]
    fn fit_inline_attachment_budget_paths() {
        // Fits whole -> returned verbatim, budget decremented by char length.
        let (txt, rem) = fit_inline_attachment_text("hello", 100, "a.txt");
        assert_eq!(txt, "hello");
        assert_eq!(rem, 95);

        // Larger than remaining but remaining >= MIN_INLINE_ATTACHMENT_SLICE ->
        // sliced to `remaining` chars + the truncation banner; budget hits 0.
        let body = "y".repeat(2000);
        let (txt, rem) = fit_inline_attachment_text(&body, 600, "/u/report.docx");
        assert_eq!(rem, 0);
        assert!(txt.starts_with(&"y".repeat(600)));
        assert!(txt.contains("[Attachment content truncated: report.docx."));
        assert!(txt.contains("Only 600 characters"));
        // Budget rendered with thousands separators (24,000).
        assert!(txt.contains("24,000-character"));

        // remaining < MIN_INLINE_ATTACHMENT_SLICE -> omitted by name, no slice.
        let (txt, rem) = fit_inline_attachment_text(&body, 100, "/u/big.xlsx");
        assert_eq!(rem, 0);
        assert!(txt.contains("[Attachment omitted from inline context: big.xlsx."));
        // The body (a run of 'y') is dropped entirely — none of it is sliced in.
        // NOTE: the Python-faithful omission banner literally contains the word
        // "already" (one 'y'), so we assert no *run* of body filler leaked rather
        // than `!txt.contains('y')` (which the banner's "already" would trip).
        assert!(!txt.contains("yy"));
        assert!(txt.contains("24,000-character"));
    }

    // -------------------------------------------------------------------
    // process_office_document — non-markitdown ext banner + real docx zip.
    // -------------------------------------------------------------------
    #[test]
    fn process_office_document_non_markitdown_ext() {
        // A .bin (not in MARKITDOWN_EXTS) -> the generic banner.
        let out = process_office_document("/x/thing.bin", "thing.bin");
        assert_eq!(out, "\n\n[Attached document file]");
    }

    #[test]
    fn process_office_document_extracts_docx_text() {
        use std::io::Write as _;
        let dir = std::env::temp_dir().join(format!("docproc_docx_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let docx = dir.join("memo.docx");

        // Build a minimal DOCX (a ZIP with word/document.xml carrying two paragraphs).
        let f = std::fs::File::create(&docx).unwrap();
        let mut zw = zip::ZipWriter::new(f);
        let opts = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated);
        zw.start_file("word/document.xml", opts).unwrap();
        zw.write_all(
            br#"<?xml version="1.0"?><w:document><w:body>
                <w:p><w:r><w:t>Hello board members.</w:t></w:r></w:p>
                <w:p><w:r><w:t>Budget &amp; plan attached.</w:t></w:r></w:p>
                </w:body></w:document>"#,
        )
        .unwrap();
        zw.finish().unwrap();

        let out = process_office_document(docx.to_str().unwrap(), "memo.docx");
        assert!(out.starts_with("\n\n[Document content — memo]:\n"));
        assert!(out.contains("Hello board members."));
        // Entity-decoded and paragraph-separated.
        assert!(out.contains("Budget & plan attached."));

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn process_office_document_blank_docx_banner() {
        use std::io::Write as _;
        let dir = std::env::temp_dir().join(format!("docproc_docx_blank_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let docx = dir.join("empty.docx");
        let f = std::fs::File::create(&docx).unwrap();
        let mut zw = zip::ZipWriter::new(f);
        let opts = zip::write::SimpleFileOptions::default();
        zw.start_file("word/document.xml", opts).unwrap();
        zw.write_all(br#"<w:document><w:body></w:body></w:document>"#).unwrap();
        zw.finish().unwrap();

        let out = process_office_document(docx.to_str().unwrap(), "empty.docx");
        assert_eq!(
            out,
            "\n\n[Attached document: empty.docx — no extractable text found.]"
        );

        let _ = std::fs::remove_dir_all(&dir);
    }

    // -------------------------------------------------------------------
    // build_user_content: resolved_uploads pre-resolution path + display name.
    // -------------------------------------------------------------------
    #[test]
    fn build_user_content_uses_resolved_uploads_map() {
        let dir = std::env::temp_dir().join(format!("docproc_resolved_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let f = dir.join("stored123.txt");
        std::fs::write(&f, "alpha\nbeta").unwrap();

        // A FakeHandler whose resolve_upload would 404 (wrong dir) — proving the
        // pre-resolved map is consulted first.
        let handler = FakeHandler::new("/nonexistent");
        // resolved_uploads is a `Map<String, Value>` whose values are JSON objects
        // (the chat path builds this from `resolve_upload`); the row for the fid is
        // consulted BEFORE the handler's resolve_upload.
        let mut resolved: Map<String, Value> = Map::new();
        let mut info = Map::new();
        info.insert("id".to_string(), Value::String("stored123.txt".to_string()));
        info.insert(
            "path".to_string(),
            Value::String(f.to_string_lossy().into_owned()),
        );
        info.insert("name".to_string(), Value::String("Quarterly.txt".to_string()));
        info.insert("mime".to_string(), Value::String("text/plain".to_string()));
        resolved.insert("stored123.txt".to_string(), Value::Object(info));

        let out = build_user_content(
            "see file",
            &["stored123.txt".to_string()],
            dir.to_str().unwrap(),
            &handler,
            None,
            None,
            Some("alice"),
            &resolved,
        );
        let s = out.as_str().expect("string");
        assert!(s.starts_with("see file"));
        // The display name from the resolved map drives the header basename.
        assert!(s.contains("=== File: stored123.txt ==="));
        assert!(s.contains("alpha\nbeta"));

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn build_user_content_unresolved_attachment_skipped() {
        // No resolved map, handler resolves nothing -> the attachment is skipped
        // with the "not found or not authorized" warning, text-only collapse.
        let out = build_user_content(
            "hi",
            &["ffffffffffffffffffffffffffffffff".to_string()],
            "/nonexistent",
            &FakeHandler::new("/nonexistent"),
            None,
            None,
            Some("bob"),
            &Map::new(),
        );
        assert_eq!(out, Value::String("hi".to_string()));
    }

    #[test]
    fn build_user_content_inline_budget_omits_later_attachment() {
        // Two big text attachments: the first consumes most of the 24k budget; the
        // second falls under MIN_INLINE_ATTACHMENT_SLICE and is omitted by name.
        let dir = std::env::temp_dir().join(format!("docproc_budget_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        // ~23.8k chars of body -> after the first attachment's header+body the
        // shared budget drops to ~135 (< MIN_INLINE_ATTACHMENT_SLICE=500).
        // (Text path caps at 30000, well above this.)
        let big = "z".repeat(23800);
        std::fs::write(dir.join("a.txt"), &big).unwrap();
        // The second file must be LARGER than the leftover budget for the
        // omission branch to fire (fit_inline_attachment_text only omits when
        // len(text) > remaining AND remaining < 500). A 16-char body would fit
        // whole inside the ~135 leftover and be inlined verbatim, so use a body
        // that comfortably exceeds the leftover. Filler 'q' appears in neither
        // the omission banner nor a.txt's 'z' filler.
        let second_body = "q".repeat(1000);
        std::fs::write(dir.join("b.txt"), &second_body).unwrap();

        let out = build_user_content(
            "docs",
            &["a.txt".to_string(), "b.txt".to_string()],
            dir.to_str().unwrap(),
            &FakeHandler::new(dir.to_str().unwrap()),
            None,
            None,
            None,
            &Map::new(),
        );
        let s = out.as_str().expect("string");
        // The second attachment is omitted-by-name, not inlined.
        assert!(s.contains("[Attachment omitted from inline context: b.txt."));
        // None of b.txt's body ('q' filler) is inlined — only the by-name banner.
        assert!(!s.contains("qqq"));

        let _ = std::fs::remove_dir_all(&dir);
    }

    // ===================================================================
    // ADVERSARIAL ROUND-TRIP: real mock OpenAI-compatible HTTP server.
    //
    // Stands up a tiny tokio TcpListener that answers `GET /models` (for
    // `resolve_model_spec`) and `POST /chat/completions` (for the vision
    // call), seeds a temp DB endpoint pointing at it, and drives the FULL
    // pipeline resolve -> build-message -> call -> parse against it. The
    // server RECORDS the last POST body so the test asserts the wire shape
    // (OpenAI `image_url` data-URI content block) matches Python.
    //
    // `#[ignore]`: mutates the process-global `DATABASE_URL` (races the other
    // DB tests in the shared lib-test binary). Run serially with:
    //   cargo test --lib -- --ignored --test-threads=1 \
    //       vl_roundtrip_against_mock_openai_server
    // ===================================================================
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    #[ignore = "mutates process-global DATABASE_URL + binds a TCP port; run with --ignored --test-threads=1"]
    async fn vl_roundtrip_against_mock_openai_server() {
        use std::sync::{Arc, Mutex};
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        // ---- mock server: records last POST body, replies canned completion ----
        let last_body: Arc<Mutex<String>> = Arc::new(Mutex::new(String::new()));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let base_url = format!("http://{addr}");

        let body_store = last_body.clone();
        let server = tokio::spawn(async move {
            loop {
                let (mut sock, _) = match listener.accept().await {
                    Ok(p) => p,
                    Err(_) => break,
                };
                let body_store = body_store.clone();
                tokio::spawn(async move {
                    // Read until we have headers; parse Content-Length, then body.
                    let mut buf = Vec::new();
                    let mut tmp = [0u8; 4096];
                    loop {
                        let n = match sock.read(&mut tmp).await {
                            Ok(0) | Err(_) => return,
                            Ok(n) => n,
                        };
                        buf.extend_from_slice(&tmp[..n]);
                        if let Some(hdr_end) = find_subslice(&buf, b"\r\n\r\n") {
                            let head = String::from_utf8_lossy(&buf[..hdr_end]).to_string();
                            let req_line = head.lines().next().unwrap_or("").to_string();
                            // Content-Length
                            let clen = head
                                .lines()
                                .find_map(|l| {
                                    let l = l.to_ascii_lowercase();
                                    l.strip_prefix("content-length:")
                                        .map(|v| v.trim().parse::<usize>().unwrap_or(0))
                                })
                                .unwrap_or(0);
                            let body_start = hdr_end + 4;
                            while buf.len() < body_start + clen {
                                let n = match sock.read(&mut tmp).await {
                                    Ok(0) | Err(_) => break,
                                    Ok(n) => n,
                                };
                                buf.extend_from_slice(&tmp[..n]);
                            }
                            let body = String::from_utf8_lossy(
                                &buf[body_start..(body_start + clen).min(buf.len())],
                            )
                            .to_string();

                            let resp_json = if req_line.contains("/models") {
                                // resolve_model_spec OpenAI probe.
                                r#"{"data":[{"id":"mock-vision"}]}"#.to_string()
                            } else {
                                // /chat/completions — record the body, reply canned.
                                *body_store.lock().unwrap() = body;
                                r#"{"choices":[{"message":{"content":"red, sunset, two people, beach, calm"}}]}"#.to_string()
                            };
                            let resp = format!(
                                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                                resp_json.len(),
                                resp_json
                            );
                            let _ = sock.write_all(resp.as_bytes()).await;
                            let _ = sock.flush().await;
                            return;
                        }
                    }
                });
            }
        });

        // ---- temp DB seeded with the mock endpoint ----
        let dir = std::env::temp_dir();
        let unique = format!(
            "odysseus_vl_rt_{}_{}.db",
            std::process::id(),
            uuid::Uuid::new_v4().simple()
        );
        let path = dir.join(unique);
        struct TmpDb(std::path::PathBuf);
        impl Drop for TmpDb {
            fn drop(&mut self) {
                let _ = std::fs::remove_file(&self.0);
            }
        }
        let _guard = TmpDb(path.clone());
        let _ = std::fs::remove_file(&path);
        std::env::set_var("DATABASE_URL", format!("sqlite:///{}", path.display()));
        crate::core::database::create_all().unwrap();
        {
            let conn = crate::core::database::session_local().unwrap();
            let ts = crate::pydatetime::utcnow_naive_iso();
            conn.execute(
                "INSERT INTO model_endpoints \
                 (id, name, base_url, api_key, is_enabled, model_type, created_at, updated_at) \
                 VALUES (?1, ?2, ?3, NULL, 1, 'llm', ?4, ?4)",
                rusqlite::params!["epmock", "Mock OpenAI", base_url, ts],
            )
            .unwrap();
        }

        // ---- (1) resolve_model_spec finds the mock model via /models probe ----
        let (url, model, headers) =
            crate::src::ai_interaction::resolve_model_spec("mock-vision", None)
                .await
                .expect("resolve_model_spec should find mock-vision");
        assert_eq!(url, format!("{base_url}/chat/completions"));
        assert_eq!(model, "mock-vision");

        // ---- (2) vl_chat: build [text, image_url] message -> call -> parse ----
        let data_uri = "data:image/png;base64,aGVsbG8="; // "hello" b64, shape only
        let reply = vl_chat(
            &url,
            &model,
            headers.clone(),
            data_uri,
            "Describe this image in detail",
            None,
            1.0,
            0,
            30,
        )
        .await
        .expect("vl_chat should return the canned completion text");
        assert_eq!(reply, "red, sunset, two people, beach, calm");

        // ---- assert the WIRE shape sent to the mock matches Python OpenAI ----
        let sent: Value = serde_json::from_str(&last_body.lock().unwrap()).unwrap();
        assert_eq!(sent["model"], json!("mock-vision"));
        // No system message was passed; the single user turn carries the
        // [text, image_url] content list, text FIRST then the data-URI image.
        let content = &sent["messages"][0]["content"];
        assert_eq!(sent["messages"][0]["role"], json!("user"));
        assert_eq!(content[0]["type"], json!("text"));
        assert_eq!(content[0]["text"], json!("Describe this image in detail"));
        assert_eq!(content[1]["type"], json!("image_url"));
        assert_eq!(content[1]["image_url"]["url"], json!(data_uri));
        // OpenAI branch carries temperature; max_tokens omitted when <= 0.
        assert_eq!(sent["temperature"], json!(1.0));
        assert!(sent.get("max_tokens").is_none());

        // ---- (3) gallery-style call: temp=0.3, max_tokens=200, full-mime URI ----
        let g_uri = "data:image/jpeg;base64,aGVsbG8=";
        let g_reply = vl_chat(
            &url, &model, headers.clone(), g_uri,
            "tags please", None, 0.3, 200, 60,
        )
        .await
        .expect("gallery vl_chat");
        // Parse tags exactly like the gallery handler does.
        let tags: Vec<String> = g_reply
            .split(',')
            .filter_map(|t| {
                let t = t.trim();
                if t.is_empty() { None } else { Some(t.to_lowercase()) }
            })
            .collect();
        assert_eq!(tags, vec!["red", "sunset", "two people", "beach", "calm"]);
        let g_sent: Value = serde_json::from_str(&last_body.lock().unwrap()).unwrap();
        assert_eq!(g_sent["temperature"], json!(0.3));
        assert_eq!(g_sent["max_tokens"], json!(200));

        // ---- (4) analyze_image_with_vl_result end-to-end (resolve auto-detect
        //          via vision_model="" would hit the candidate list; here the
        //          configured model is non-empty so it resolves to the mock) ----
        // Write a settings file so _load_vl_settings returns vision_model=mock-vision.
        // Instead of mutating global settings, exercise the async core's resolve+call
        // through resolve_vl_model(configured) + vl_chat (the same path it runs).
        let (rurl, rmodel, rheaders) = resolve_vl_model("mock-vision")
            .await
            .expect("resolve_vl_model(configured) -> mock");
        let img_reply = vl_chat(
            &rurl, &rmodel, rheaders, "data:image/png;base64,aGVsbG8=",
            "Describe this image in detail", None, 1.0, 0, 30,
        )
        .await
        .expect("resolve_vl_model + vl_chat round-trip");
        assert_eq!(img_reply, "red, sunset, two people, beach, calm");

        server.abort();
    }

    /// Find the first index of `needle` in `hay` (tiny, test-only).
    fn find_subslice(hay: &[u8], needle: &[u8]) -> Option<usize> {
        hay.windows(needle.len()).position(|w| w == needle)
    }
}
