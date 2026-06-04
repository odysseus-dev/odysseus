// src/pdf_form_doc.rs  <- src/pdf_form_doc.py
//! Bridge between extracted PDF form fields and the document editor.
//!
//! Design: the user edits the form as readable markdown — labels as bullets,
//! values as plain text — exactly like any other document in the editor.
//!
//! A hidden HTML-comment front-matter pointer at the top of the markdown
//! links the document back to the source PDF and the field-schema sidecar:
//!
//! ```text
//!     <!-- pdf_form_source upload_id="abc.pdf" fields="441" -->
//! ```
//!
//! The export route reads that pointer to find the source PDF + sidecar JSON,
//! then asks an LLM to map markdown values back to AcroForm field names.
//!
//! PORT NOTE: this module touches NO PDF binaries — it is pure markdown + JSON
//! + DB. It lives with the rest of the PDF cluster for cohesion, and because
//! `create_*_document` reuses `documents.rs`'s DB INSERT pattern and
//! `set_active_document`. Faithful PORT_NOW; no deferrals.

use once_cell::sync::Lazy;
use percent_encoding::percent_decode_str;
use regex::Regex;
use serde_json::{Map, Value};
use std::collections::HashSet;

use crate::core::database::session_local;
use crate::pylog as logger;

// ---------------------------------------------------------------------------
// Regexes  (src/pdf_form_doc.py L25-L34, L93-L106, L129-L131)
// ---------------------------------------------------------------------------

static FRONT_MATTER_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r#"<!--\s*pdf_form_source\s+upload_id="(?P<upload_id>[^"]+)"(?:\s+fields="(?P<fields>\d+)")?\s*-->"#,
    )
    .unwrap()
});

// Freeform annotation bullet — mirrors the JS regex in static/js/document.js.
// Coords are page percentages (0–100); kind/lh are optional for backward compat.
//
// PORT NOTE: the Python regex is compiled with `re.MULTILINE` and applied via
// `finditer` over the whole content. We compile with `(?m)` and iterate
// `find_iter`. The Python uses `^[ \t]*` / `[ \t]*$` line anchors which behave
// identically under the `regex` crate's multiline mode.
static ANNOTATION_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?m)^[ \t]*-\s+(?P<value>.*?)\s*<!--\s*annotation\s+id=(?P<id>[\w-]+)\s+page=(?P<page>\d+)\s+x=(?P<x>[\d.]+)\s+y=(?P<y>[\d.]+)\s+w=(?P<w>[\d.]+)\s+h=(?P<h>[\d.]+)(?:\s+kind=(?P<kind>\w+))?(?:\s+lh=(?P<lh>[\d.]+))?\s*-->[ \t]*$",
    )
    .unwrap()
});

// Plain-PDF marker: same shape as the form-source marker but emitted for any
// imported PDF (no AcroForm fields).
static PLAIN_FRONT_MATTER_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r#"<!--\s*pdf_source\s+upload_id="(?P<upload_id>[^"]+)"\s*-->"#).unwrap()
});

// Bullet line emitted by render_form_as_markdown. The trailing comment is the
// anchor we rely on to recover the field name even after the user/model edits
// the value. The field name is percent-encoded so spaces, newlines, parens
// and other special chars in raw AcroForm names don't break parsing.
//   - **label:** value <!-- field=NAME-ENC type=text -->
//   - **label** [opts]: value <!-- field=NAME-ENC type=choice -->
//   - [x] **label** <!-- field=NAME-ENC type=checkbox -->
//
// PORT NOTE: applied per-line via `.match(line)` in Python, i.e. anchored at the
// start of each line. We use the same `^...$` and call `.captures` against each
// `splitlines()` slice.
static FIELD_BULLET_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"^\s*-\s+(?P<body>.*?)\s*<!--\s*field=(?P<name>[A-Za-z0-9_.%-]+)\s+type=(?P<type>\w+)\s*-->\s*$",
    )
    .unwrap()
});

// Label segment is non-greedy (.+?) so labels containing '*' — the near-universal
// required-field marker, e.g. "Email *" — are tolerated, while still splitting at
// the FIRST ':**' / '**[' so a value that itself contains ':**' is preserved.
// (The old [^*]+ refused to match any label with an asterisk and silently
// dropped that field's value on export.)
static TEXT_VALUE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"\*\*.+?:\*\*\s*(?P<value>.*)$").unwrap());
static CHOICE_VALUE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"\*\*.+?\*\*\s*\[[^\]]*\]\s*:\s*(?P<value>.*)$").unwrap());
static CHECKBOX_VALUE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^\s*\[(?P<state>[xX ])\]").unwrap());

// Whitespace collapse for _flatten (re.sub(r"\s+", " ", ...)).
static WHITESPACE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\s+").unwrap());

static PLACEHOLDERS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    ["_(empty)_", "_(not selected)_", "_(empty)_.", "_(unsigned)_"]
        .into_iter()
        .collect()
});

// ---------------------------------------------------------------------------
// _unescape_annotation_value  (src/pdf_form_doc.py L37-L56)
// ---------------------------------------------------------------------------

/// Inverse of the JS `_escapeAnnotationValue`: `\n` -> newline, `\\` -> `\`.
fn unescape_annotation_value(s: &str) -> String {
    let mut out = String::new();
    let chars: Vec<char> = s.chars().collect();
    let n = chars.len();
    let mut i = 0;
    while i < n {
        let ch = chars[i];
        if ch == '\\' && i + 1 < n {
            let nxt = chars[i + 1];
            match nxt {
                'n' => out.push('\n'),
                '\\' => out.push('\\'),
                _ => out.push(nxt),
            }
            i += 2;
        } else {
            out.push(ch);
            i += 1;
        }
    }
    out
}

// ---------------------------------------------------------------------------
// parse_markdown_annotations  (src/pdf_form_doc.py L59-L88)
// ---------------------------------------------------------------------------

/// Return the list of freeform annotation dicts embedded in a doc's markdown.
///
/// Each entry: `{id, page, x, y, w, h, kind, line_height, value}`.
/// Coordinates are page percentages (0–100) — caller scales them to PDF user
/// units when stamping.
pub fn parse_markdown_annotations(content: &str) -> Vec<Map<String, Value>> {
    let mut out: Vec<Map<String, Value>> = Vec::new();
    for m in ANNOTATION_RE.captures_iter(content) {
        // One malformed bullet (e.g. user hand-edited markdown leaving
        // `x=12.3.4`) must NOT drop every other annotation in the doc.
        // Skip the bad line, keep going.
        //
        // PORT NOTE: the Python `try/except (ValueError, TypeError)` guards the
        // int()/float() conversions. The named groups here all match `[\d.]+`
        // for the numerics, so `parse` should not fail for a matched bullet; we
        // still guard each parse and `continue` on the first failure, logging at
        // the match offset, to preserve the per-bullet skip semantics exactly.
        let raw = m.name("value").map(|x| x.as_str()).unwrap_or("");
        let value = if raw == "_(empty)_" {
            String::new()
        } else {
            unescape_annotation_value(raw)
        };

        let parse_f = |key: &str| -> Option<f64> {
            m.name(key).and_then(|x| x.as_str().parse::<f64>().ok())
        };
        let parse_i = |key: &str| -> Option<i64> {
            m.name(key).and_then(|x| x.as_str().parse::<i64>().ok())
        };

        let page = match parse_i("page") {
            Some(v) => v,
            None => {
                logger::warning(&format!(
                    "Skipping malformed annotation bullet near offset {}: invalid page",
                    m.get(0).map(|g| g.start()).unwrap_or(0)
                ));
                continue;
            }
        };
        let (x, y, w, h) = match (parse_f("x"), parse_f("y"), parse_f("w"), parse_f("h")) {
            (Some(x), Some(y), Some(w), Some(h)) => (x, y, w, h),
            _ => {
                logger::warning(&format!(
                    "Skipping malformed annotation bullet near offset {}: invalid coords",
                    m.get(0).map(|g| g.start()).unwrap_or(0)
                ));
                continue;
            }
        };
        // `float(lh) if lh else 1.3` — empty/absent group -> default 1.3.
        let line_height = match m.name("lh").map(|x| x.as_str()) {
            Some(s) if !s.is_empty() => match s.parse::<f64>() {
                Ok(v) => v,
                Err(_) => {
                    logger::warning(&format!(
                        "Skipping malformed annotation bullet near offset {}: invalid lh",
                        m.get(0).map(|g| g.start()).unwrap_or(0)
                    ));
                    continue;
                }
            },
            _ => 1.3,
        };
        // `m.group("kind") or "text"`.
        let kind = match m.name("kind").map(|x| x.as_str()) {
            Some(s) if !s.is_empty() => s.to_string(),
            _ => "text".to_string(),
        };

        let mut entry = Map::new();
        entry.insert(
            "id".to_string(),
            Value::String(m.name("id").map(|x| x.as_str()).unwrap_or("").to_string()),
        );
        entry.insert("page".to_string(), Value::from(page));
        entry.insert("x".to_string(), json_f64(x));
        entry.insert("y".to_string(), json_f64(y));
        entry.insert("w".to_string(), json_f64(w));
        entry.insert("h".to_string(), json_f64(h));
        entry.insert("kind".to_string(), Value::String(kind));
        entry.insert("line_height".to_string(), json_f64(line_height));
        entry.insert("value".to_string(), Value::String(value));
        out.push(entry);
    }
    out
}

/// `serde_json::Number` from an `f64` (falls back to Null on NaN/Inf, which the
/// `[\d.]+` regex inputs never produce).
fn json_f64(v: f64) -> Value {
    serde_json::Number::from_f64(v)
        .map(Value::Number)
        .unwrap_or(Value::Null)
}

// ---------------------------------------------------------------------------
// _encode_name / _decode_name  (src/pdf_form_doc.py L109-L128)
// ---------------------------------------------------------------------------

/// Percent-encode any char that's not a regex/HTML-comment-safe token.
///
/// Keeps A-Z a-z 0-9 _ . - . Everything else (spaces, newlines, parens,
/// commas, quotes, etc.) becomes %XX. JS side must use the same scheme.
fn encode_name(name: &str) -> String {
    let mut out = String::new();
    for ch in name.chars() {
        // Python `ch.isalnum()` is Unicode-aware. We mirror that with
        // `char::is_alphanumeric`, plus the explicit `_ . -` allowlist.
        if ch.is_alphanumeric() || ch == '_' || ch == '.' || ch == '-' {
            out.push(ch);
        } else {
            let mut buf = [0u8; 4];
            for b in ch.encode_utf8(&mut buf).as_bytes() {
                out.push_str(&format!("%{b:02X}"));
            }
        }
    }
    out
}

/// Inverse of `_encode_name` (`urllib.parse.unquote`).
fn decode_name(enc: &str) -> String {
    percent_decode_str(enc).decode_utf8_lossy().into_owned()
}

// ---------------------------------------------------------------------------
// sidecar load/save  (src/pdf_form_doc.py L136-L162)
// ---------------------------------------------------------------------------

/// Path of the field-schema JSON stored next to a PDF upload.
pub fn sidecar_path(pdf_path: &str) -> String {
    format!("{pdf_path}.fields.json")
}

/// Persist the field schema next to its source PDF. Returns the sidecar path.
pub fn save_field_sidecar(pdf_path: &str, fields: &[Value]) -> String {
    let path = sidecar_path(pdf_path);
    // Python: json.dump(fields, f, indent=2). serde_json's pretty printer uses
    // two-space indent, matching `indent=2`.
    match serde_json::to_string_pretty(fields) {
        Ok(s) => {
            if let Err(e) = std::fs::write(&path, s) {
                logger::warning(&format!("Failed to write field sidecar {path}: {e}"));
            }
        }
        Err(e) => logger::warning(&format!("Failed to write field sidecar {path}: {e}")),
    }
    path
}

/// Return field schema for a PDF, or None if no sidecar exists.
pub fn load_field_sidecar(pdf_path: &str) -> Option<Vec<Value>> {
    let path = sidecar_path(pdf_path);
    if !std::path::Path::new(&path).exists() {
        return None;
    }
    match std::fs::read_to_string(&path) {
        Ok(s) => match serde_json::from_str::<Vec<Value>>(&s) {
            Ok(v) => Some(v),
            Err(e) => {
                logger::warning(&format!("Failed to read field sidecar {path}: {e}"));
                None
            }
        },
        Err(e) => {
            logger::warning(&format!("Failed to read field sidecar {path}: {e}"));
            None
        }
    }
}

// ---------------------------------------------------------------------------
// find_source_upload_id  (src/pdf_form_doc.py L165-L172)
// ---------------------------------------------------------------------------

/// Return the upload_id from the doc's front-matter pointer, or None.
///
/// Matches both the form-source marker (`pdf_form_source`) used for fillable
/// PDFs and the plain marker (`pdf_source`) used for any imported PDF.
/// Rejects malformed ids (path traversal, wrong shape) before any lookup.
pub fn find_source_upload_id(content: &str) -> Option<String> {
    let m = FRONT_MATTER_RE
        .captures(content)
        .or_else(|| PLAIN_FRONT_MATTER_RE.captures(content));
    let upload_id = m.and_then(|c| c.name("upload_id").map(|x| x.as_str().to_string()))?;
    if !crate::src::upload_handler::validate_upload_id(&upload_id) {
        logger::warning(&format!(
            "Ignoring invalid pdf_source upload_id in document content: {:?}",
            upload_id
        ));
        return None;
    }
    Some(upload_id)
}

// ---------------------------------------------------------------------------
// render_plain_pdf_markdown  (src/pdf_form_doc.py L175-L192)
// ---------------------------------------------------------------------------

/// Build the markdown wrapper for a non-form PDF imported into the editor.
///
/// The hidden front-matter pointer links the doc to the source PDF so the
/// viewer endpoints (render-pages / page-png) can serve the rendered pages.
/// Any extracted text is included below the title so the markdown source view
/// is still useful (search, copy/paste, AI tools).
pub fn render_plain_pdf_markdown(upload_id: &str, title: &str, body_text: Option<&str>) -> String {
    let mut lines: Vec<String> = vec![
        format!("<!-- pdf_source upload_id=\"{upload_id}\" -->"),
        String::new(),
        format!("# {title}"),
        String::new(),
    ];
    if let Some(bt) = body_text {
        if !bt.trim().is_empty() {
            lines.push(bt.trim().to_string());
            lines.push(String::new());
        }
    }
    format!("{}\n", lines.join("\n"))
}

// ---------------------------------------------------------------------------
// create_plain_pdf_document  (src/pdf_form_doc.py L195-L244)
// ---------------------------------------------------------------------------

/// Create a markdown Document for a non-form PDF and set it active.
///
/// Returns the new doc_id, or None on failure. Pairs with `find_source_upload_id`
/// so the existing /render-pages and /page/{n}.png endpoints can serve the
/// pages without form-field overlays.
pub fn create_plain_pdf_document(
    session_id: &str,
    upload_id: &str,
    title: &str,
    body_text: Option<&str>,
) -> Option<String> {
    let content = render_plain_pdf_markdown(upload_id, title, body_text);
    create_document_row(session_id, title, &content, "Imported from PDF")
}

// ---------------------------------------------------------------------------
// parse_markdown_to_values  (src/pdf_form_doc.py L247-L285)
// ---------------------------------------------------------------------------

/// Recover `{field_name: value}` from the rendered markdown.
///
/// Deterministic — relies on the hidden HTML-comment field markers in each
/// bullet. Lines whose markers are intact survive arbitrary edits to label
/// and value text. Lines whose markers were stripped are silently skipped;
/// those fields just won't be filled in the output PDF.
///
/// Empty placeholders ("_(empty)_", "_(not selected)_") map to "".
/// Checkbox state comes from the leading `[ ]` / `[x]` marker.
///
/// PORT NOTE: values are heterogeneous in Python (`bool` for checkboxes, `str`
/// otherwise). Modeled here as a `serde_json::Map` (`preserve_order` keeps the
/// dict insertion order). Checkboxes become `Value::Bool`, others `Value::String`.
pub fn parse_markdown_to_values(content: &str) -> Map<String, Value> {
    let mut values: Map<String, Value> = Map::new();
    for line in content.split('\n') {
        // Python `splitlines()` does not include the line terminator; `split('\n')`
        // on content with `\r\n` would leave a trailing `\r`, but the bullet regex
        // ends in `$` (matching before an optional trailing `\r` is NOT default in
        // the `regex` crate). To stay faithful to `str.splitlines()` (which also
        // splits on `\r`, `\r\n`, and other line boundaries), strip a trailing
        // `\r` from each line here. `\r`-only and other Unicode line breaks are not
        // exercised by the editor (it emits `\n`), so this covers the real inputs.
        let line = line.strip_suffix('\r').unwrap_or(line);
        let m = match FIELD_BULLET_RE.captures(line) {
            Some(m) => m,
            None => continue,
        };
        let name = decode_name(m.name("name").map(|x| x.as_str()).unwrap_or(""));
        let ftype = m.name("type").map(|x| x.as_str()).unwrap_or("");
        let body = m.name("body").map(|x| x.as_str()).unwrap_or("");

        if ftype == "checkbox" {
            let cb = CHECKBOX_VALUE_RE.captures(body);
            let checked = cb
                .and_then(|c| c.name("state").map(|x| x.as_str().to_lowercase()))
                .map(|s| s == "x")
                .unwrap_or(false);
            values.insert(name, Value::Bool(checked));
            continue;
        }

        let mut raw = String::new();
        if ftype == "choice" {
            if let Some(cm) = CHOICE_VALUE_RE.captures(body) {
                raw = cm.name("value").map(|x| x.as_str().trim()).unwrap_or("").to_string();
            }
        } else if let Some(tm) = TEXT_VALUE_RE.captures(body) {
            raw = tm.name("value").map(|x| x.as_str().trim()).unwrap_or("").to_string();
        }

        if PLACEHOLDERS.contains(raw.as_str()) {
            raw = String::new();
        }
        values.insert(name, Value::String(raw));
    }
    values
}

// ---------------------------------------------------------------------------
// bullet formatting helpers  (src/pdf_form_doc.py L288-L330)
// ---------------------------------------------------------------------------

fn checkbox_marker(value: bool) -> &'static str {
    if value {
        "[x]"
    } else {
        "[ ]"
    }
}

/// Collapse PDF newline runs (`\r`, `\n`) so a value fits on one bullet line.
///
/// Python: `re.sub(r"\s+", " ", str(value)).strip()`; `None -> ""`.
fn flatten(value: Option<&Value>) -> String {
    match value {
        None | Some(Value::Null) => String::new(),
        Some(v) => {
            let s = value_to_py_str(v);
            WHITESPACE_RE.replace_all(&s, " ").trim().to_string()
        }
    }
}

/// `str(value)` parity for the JSON values that appear in a field dict (string,
/// bool, number). Mirrors Python's `str()` for the shapes the sidecar carries.
fn value_to_py_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
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

/// Render one form field as a markdown bullet line.
///
/// Hidden HTML comment carries the percent-encoded field name so the
/// export/save logic has a robust anchor regardless of what whitespace,
/// parens, or special chars appear in the raw AcroForm field name. The
/// visible label is the human-readable bit.
///
/// Signature fields encode the chosen signature ID inline as
/// `signature:<id>` so the picker selection persists in the doc and the
/// export route can stamp the saved PNG without extra state.
fn format_field_bullet(f: &Map<String, Value>) -> String {
    // label = _flatten(f.get("label")) or f["name"]
    let flattened_label = flatten(f.get("label"));
    let name_str = f
        .get("name")
        .map(value_to_py_str)
        .unwrap_or_default();
    let label = if flattened_label.is_empty() {
        name_str.clone()
    } else {
        flattened_label
    };
    let name = encode_name(&name_str);
    let ftype = f.get("type").map(value_to_py_str).unwrap_or_default();
    let value = flatten(f.get("value"));

    let body = if ftype == "checkbox" {
        // _checkbox_marker(value) where `value` is the flattened string — Python
        // passes the flattened string here, so any non-empty string is truthy.
        format!("{} **{label}**", checkbox_marker(!value.is_empty()))
    } else if ftype == "choice" {
        // opts = f.get("options") or []
        let opts_str = match f.get("options") {
            Some(Value::Array(opts)) if !opts.is_empty() => opts
                .iter()
                .map(value_to_py_str)
                .collect::<Vec<_>>()
                .join(" / "),
            _ => String::new(),
        };
        let shown = if !value.is_empty() {
            value.clone()
        } else {
            "_(not selected)_".to_string()
        };
        format!("**{label}** [{opts_str}]: {shown}")
    } else if ftype == "signature" {
        let shown = if !value.is_empty() && value.starts_with("signature:") {
            value.clone()
        } else {
            "_(unsigned)_".to_string()
        };
        format!("**{label}:** {shown}")
    } else {
        let shown = if !value.is_empty() {
            value.clone()
        } else {
            "_(empty)_".to_string()
        };
        format!("**{label}:** {shown}")
    };

    format!("- {body} <!-- field={name} type={ftype} -->")
}

// ---------------------------------------------------------------------------
// render_form_as_markdown  (src/pdf_form_doc.py L333-L374)
// ---------------------------------------------------------------------------

/// Build the markdown document the user edits in the editor.
///
/// Layout:
///   front-matter pointer (hidden in editor render but present in source)
///   title
///   one-paragraph intro + how to export
///   one section per page, bulleted fields
pub fn render_form_as_markdown(
    fields: &[Value],
    upload_id: &str,
    title: &str,
    intro_text: Option<&str>,
) -> String {
    let mut lines: Vec<String> = vec![
        format!(
            "<!-- pdf_form_source upload_id=\"{upload_id}\" fields=\"{}\" -->",
            fields.len()
        ),
        String::new(),
        format!("# {title}"),
        String::new(),
        "Edit values in place — change the text after each label, tick/untick \
checkboxes, and pick one of the listed options for choice fields. \
When done, click **Export PDF** to download the filled form."
            .to_string(),
        String::new(),
    ];

    let mut last_page: Option<i64> = None;
    for f in fields {
        let fmap = match f.as_object() {
            Some(m) => m,
            // Python indexes f["page"] / _format_field_bullet(f) which would
            // raise on a non-dict; the sidecar always carries dicts. Skip a
            // stray non-object defensively.
            None => continue,
        };
        // Python `f["page"]` (KeyError if absent) — page is always present in the
        // schema produced by pdf_forms.extract_fields.
        let page = fmap.get("page").and_then(|v| v.as_i64());
        if page != last_page {
            lines.push(String::new());
            lines.push(format!(
                "## Page {}",
                page.map(|p| p.to_string()).unwrap_or_default()
            ));
            lines.push(String::new());
            last_page = page;
        }
        lines.push(format_field_bullet(fmap));
    }

    if let Some(intro) = intro_text {
        if !intro.is_empty() {
            lines.push(String::new());
            lines.push("---".to_string());
            lines.push(String::new());
            lines.push("## Original form text".to_string());
            lines.push(String::new());
            lines.push(intro.trim().to_string());
        }
    }

    format!("{}\n", lines.join("\n"))
}

// ---------------------------------------------------------------------------
// create_form_markdown_document  (src/pdf_form_doc.py L377-L427)
// ---------------------------------------------------------------------------

/// Create a markdown Document for an editable form and set it active.
///
/// Returns the new doc_id, or None on failure. The Document's language is
/// "markdown" — the form-ness is signalled only by the front-matter pointer
/// inside the content, which the export route looks for.
pub fn create_form_markdown_document(
    session_id: &str,
    fields: &[Value],
    upload_id: &str,
    title: &str,
    intro_text: Option<&str>,
) -> Option<String> {
    let content = render_form_as_markdown(fields, upload_id, title, intro_text);
    create_document_row(session_id, title, &content, "Imported from PDF form")
}

// ---------------------------------------------------------------------------
// Shared DB INSERT (mirrors documents.rs do_create_document INSERT pattern)
// ---------------------------------------------------------------------------

/// The shared DB body for `create_plain_pdf_document` / `create_form_markdown_document`.
///
/// Mirrors the Python pattern in both functions verbatim (only the version
/// `summary` differs): insert a `documents` row (owner inherited from the
/// session) + a v1 `document_versions` row with `source='upload'`, commit, set
/// active. On any DB failure, roll back implicitly (a fresh connection is
/// dropped without commit) and return None — matching Python's
/// `db.rollback(); return None`.
fn create_document_row(
    session_id: &str,
    title: &str,
    content: &str,
    version_summary: &str,
) -> Option<String> {
    let result: rusqlite::Result<String> = (|| {
        let conn = session_local()?;
        let doc_id = uuid::Uuid::new_v4().to_string();
        let ver_id = uuid::Uuid::new_v4().to_string();

        // owner = _sess.owner if _sess else None.
        let owner: Option<String> = conn
            .query_row(
                "SELECT owner FROM sessions WHERE id = ?1",
                [session_id],
                |r| r.get::<_, Option<String>>(0),
            )
            .ok()
            .flatten();

        // SQLAlchemy fills created_at/updated_at; the Rust port writes naive-UTC
        // ISO strings explicitly (the documents table has both NOT NULL).
        let now = crate::pydatetime::utcnow_naive_iso();
        conn.execute(
            "INSERT INTO documents \
               (id, session_id, title, language, current_content, version_count, \
                is_active, archived, owner, created_at, updated_at) \
             VALUES (?1, ?2, ?3, 'markdown', ?4, 1, 1, 0, ?5, ?6, ?6)",
            rusqlite::params![doc_id, session_id, title, content, owner, now],
        )?;
        conn.execute(
            "INSERT INTO document_versions \
               (id, document_id, version_number, content, summary, source, created_at) \
             VALUES (?1, ?2, 1, ?3, ?4, 'upload', ?5)",
            rusqlite::params![ver_id, doc_id, content, version_summary, now],
        )?;
        Ok(doc_id)
    })();

    match result {
        Ok(doc_id) => {
            crate::src::tool_implementations::documents::set_active_document(Some(&doc_id));
            Some(doc_id)
        }
        Err(e) => {
            logger::error(&format!(
                "Failed to create {} document: {e}",
                if version_summary == "Imported from PDF form" {
                    "form markdown"
                } else {
                    "plain PDF"
                }
            ));
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn field(map: Value) -> Value {
        map
    }

    #[test]
    fn encode_decode_round_trip() {
        for raw in ["First Name", "a.b-c_d", "weird (paren), \"q\"\nnl", "café/x"] {
            let enc = encode_name(raw);
            assert_eq!(decode_name(&enc), raw, "round-trip failed for {raw:?}");
        }
        // Space -> %20; comma -> %2C; newline -> %0A.
        assert_eq!(encode_name("a b,c\n"), "a%20b%2Cc%0A");
    }

    #[test]
    fn format_text_field_bullet() {
        let f = json!({
            "name": "First Name",
            "type": "text",
            "label": "First name",
            "value": "Alice",
        });
        let bullet = format_field_bullet(f.as_object().unwrap());
        assert_eq!(
            bullet,
            "- **First name:** Alice <!-- field=First%20Name type=text -->"
        );
    }

    #[test]
    fn format_empty_text_uses_placeholder() {
        let f = json!({"name": "x", "type": "text", "label": "", "value": ""});
        let bullet = format_field_bullet(f.as_object().unwrap());
        assert_eq!(bullet, "- **x:** _(empty)_ <!-- field=x type=text -->");
    }

    #[test]
    fn format_checkbox_and_choice() {
        let cb = json!({"name": "agree", "type": "checkbox", "label": "Agree", "value": "Yes"});
        assert_eq!(
            format_field_bullet(cb.as_object().unwrap()),
            "- [x] **Agree** <!-- field=agree type=checkbox -->"
        );
        let cb_off = json!({"name": "agree", "type": "checkbox", "label": "Agree", "value": ""});
        assert_eq!(
            format_field_bullet(cb_off.as_object().unwrap()),
            "- [ ] **Agree** <!-- field=agree type=checkbox -->"
        );
        let choice = json!({
            "name": "status", "type": "choice", "label": "Status",
            "value": "", "options": ["Yes", "No"]
        });
        assert_eq!(
            format_field_bullet(choice.as_object().unwrap()),
            "- **Status** [Yes / No]: _(not selected)_ <!-- field=status type=choice -->"
        );
    }

    #[test]
    fn parse_round_trips_text_choice_checkbox() {
        let fields = vec![
            field(json!({"name": "First Name", "type": "text", "label": "First", "value": "Bob"})),
            field(json!({"name": "agree", "type": "checkbox", "label": "Agree", "value": "Yes"})),
            field(json!({
                "name": "status", "type": "choice", "label": "Status",
                "value": "No", "options": ["Yes", "No"], "page": 1
            })),
        ];
        let md = render_form_as_markdown(&fields, "abc.pdf", "Form", None);
        let values = parse_markdown_to_values(&md);
        assert_eq!(values.get("First Name"), Some(&Value::String("Bob".to_string())));
        assert_eq!(values.get("agree"), Some(&Value::Bool(true)));
        assert_eq!(values.get("status"), Some(&Value::String("No".to_string())));
    }

    #[test]
    fn front_matter_round_trip() {
        // Use a syntactically valid upload_id (32 hex chars + extension) so
        // find_source_upload_id's validate_upload_id gate passes.
        let uid = "0123456789abcdef0123456789abcdef.pdf";

        let md = render_form_as_markdown(
            &[json!({"name": "x", "type": "text", "label": "X", "value": "", "page": 1})],
            uid,
            "My Form",
            None,
        );
        assert!(md.contains(&format!("<!-- pdf_form_source upload_id=\"{uid}\" fields=\"1\" -->")));
        assert_eq!(find_source_upload_id(&md), Some(uid.to_string()));

        let plain = render_plain_pdf_markdown(uid, "T", Some("body"));
        assert!(plain.contains(&format!("<!-- pdf_source upload_id=\"{uid}\" -->")));
        assert_eq!(find_source_upload_id(&plain), Some(uid.to_string()));
    }

    #[test]
    fn find_source_upload_id_rejects_invalid() {
        // Path traversal / short / non-hex — must return None.
        let traversal =
            "<!-- pdf_source upload_id=\"../../../etc/passwd\" -->";
        assert_eq!(find_source_upload_id(traversal), None);

        let short_id = "<!-- pdf_form_source upload_id=\"doc123.pdf\" fields=\"1\" -->";
        assert_eq!(find_source_upload_id(short_id), None);

        // No marker at all.
        assert_eq!(find_source_upload_id("plain content no pointer"), None);
    }

    #[test]
    fn placeholder_values_map_to_empty() {
        let md = "- **L:** _(empty)_ <!-- field=a type=text -->\n\
                  - **L** [x / y]: _(not selected)_ <!-- field=b type=choice -->";
        let values = parse_markdown_to_values(md);
        assert_eq!(values.get("a"), Some(&Value::String(String::new())));
        assert_eq!(values.get("b"), Some(&Value::String(String::new())));
    }

    #[test]
    fn annotations_skip_malformed_keep_rest() {
        let content = "\
- good text <!-- annotation id=a1 page=1 x=10.0 y=20.0 w=30.0 h=5.0 kind=text lh=1.5 -->
- broken <!-- annotation id=a2 page=1 x=10.0 y=20.0 w=30.0 h=5.0 -->
- second <!-- annotation id=a3 page=2 x=1.0 y=2.0 w=3.0 h=4.0 kind=check -->";
        let anns = parse_markdown_annotations(content);
        // The "broken" one is actually well-formed (missing kind/lh -> defaults),
        // so all three parse. Verify defaults applied.
        assert_eq!(anns.len(), 3);
        assert_eq!(anns[0].get("kind"), Some(&Value::String("text".to_string())));
        assert_eq!(anns[0].get("line_height"), Some(&json_f64(1.5)));
        assert_eq!(anns[1].get("kind"), Some(&Value::String("text".to_string())));
        assert_eq!(anns[1].get("line_height"), Some(&json_f64(1.3)));
        assert_eq!(anns[2].get("kind"), Some(&Value::String("check".to_string())));
        assert_eq!(anns[2].get("page"), Some(&Value::from(2_i64)));
    }

    #[test]
    fn annotation_empty_placeholder_value() {
        let content = "- _(empty)_ <!-- annotation id=a1 page=1 x=0 y=0 w=10 h=10 -->";
        let anns = parse_markdown_annotations(content);
        assert_eq!(anns.len(), 1);
        assert_eq!(anns[0].get("value"), Some(&Value::String(String::new())));
    }

    #[test]
    fn annotation_escapes_unescaped() {
        let content = r"- line1\nline2\\end <!-- annotation id=a1 page=1 x=0 y=0 w=10 h=10 -->";
        let anns = parse_markdown_annotations(content);
        assert_eq!(
            anns[0].get("value"),
            Some(&Value::String("line1\nline2\\end".to_string()))
        );
    }

    /// Labels containing '*' (e.g. "Email *" — a common required-field marker)
    /// must still be matched by TEXT_VALUE_RE / CHOICE_VALUE_RE now that the
    /// pattern uses non-greedy `.+?` instead of the old `[^*]+`.
    #[test]
    fn asterisk_label_parsed_correctly() {
        // text field: "**Email *:** alice@example.com <!-- field=email type=text -->"
        let text_line =
            "- **Email *:** alice@example.com <!-- field=email type=text -->";
        let values = parse_markdown_to_values(text_line);
        assert_eq!(
            values.get("email"),
            Some(&Value::String("alice@example.com".to_string())),
            "text field with '*' in label should parse its value"
        );

        // choice field: "**Size *** [S / M / L]: M <!-- field=sz type=choice -->"
        let choice_line =
            "- **Size *** [S / M / L]: M <!-- field=sz type=choice -->";
        let values2 = parse_markdown_to_values(choice_line);
        assert_eq!(
            values2.get("sz"),
            Some(&Value::String("M".to_string())),
            "choice field with '*' in label should parse its value"
        );
    }
}
