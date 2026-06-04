// src/tool_implementations/documents.rs  <- src/tool_implementations.py (documents group)
//! Document tools — create/update/edit/suggest/manage living documents.
//!
//! Ports the `do_create_document` / `do_update_document` / `do_edit_document` /
//! `do_suggest_document` / `do_manage_documents` tools plus their pure helpers
//! (`_sniff_doc_language`, `_looks_like_email_document`,
//! `_coerce_email_document_content`, `parse_edit_blocks`, `parse_suggest_blocks`)
//! and the active-document / active-model module globals.
//!
//! OWNER SCOPING: every document tool that reads/updates/deletes an existing
//! document takes an `owner: Option<&str>` and routes its fetch through the
//! owner-aware helpers (`fetch_doc_owned` / `fetch_doc_most_recent_owned`). When
//! `owner` is `None` these return ZERO rows (the SQLAlchemy `false()` literal in
//! `_owned_document_query`), so an unscoped call can never read another tenant's
//! document. `do_create_document` additionally refuses to create a document in a
//! session whose `owner` does not match the caller.
//!
//! TOOL RESULT TYPE: every `do_*` returns the Python `dict` as a
//! `serde_json::Map<String, Value>` (serde_json `preserve_order` is ON, so
//! insertion order = Python dict order). Errors are RETURNED as error Maps, NOT
//! propagated as `PyError` — the Python `try/except` becomes "catch and return".
//!
//! NOTE on error-Map shape: `do_create_document` / `do_update_document` /
//! `do_edit_document` / `do_suggest_document` return bare `{"error": <str>}`
//! (no `exit_code`) — faithfully reproduced here, so they do NOT use the shared
//! `err_result` (which adds `exit_code: 1`). Only `do_manage_documents` returns
//! `{"error": ..., "exit_code": 1}` (and `{..., "exit_code": 0}` on success).
//!
//! DB ACCESS: a fresh `rusqlite::Connection` per call via
//! `crate::core::database::session_local()`; raw SQL on `documents` /
//! `document_versions`. Timestamps are written as naive-UTC strings via
//! `crate::pydatetime::utcnow_naive_iso()` to match SQLAlchemy's SQLite
//! `DateTime` storage format (and `updated_at` is set explicitly since SQLite has
//! no `onupdate` equivalent).

use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{Map, Value};
use std::collections::HashSet;
use std::sync::Mutex;

use crate::core::database::session_local;
use crate::pylog as logger;
use crate::src::agent_tools::MAX_READ_CHARS;

// ---------------------------------------------------------------------------
// Active document / model state  (src/tool_implementations.py L71-88)
// ---------------------------------------------------------------------------
//
// Python module globals `_active_document_id` / `_active_model`. Modeled as
// process-global `Mutex<Option<String>>` so `set_active_*` (called by the agent
// loop) and the `do_*` readers below share the same slot.

static ACTIVE_DOCUMENT_ID: Lazy<Mutex<Option<String>>> = Lazy::new(|| Mutex::new(None));
static ACTIVE_MODEL: Lazy<Mutex<Option<String>>> = Lazy::new(|| Mutex::new(None));

/// `set_active_document(doc_id)` — set the active document ID for document tool
/// execution.
pub fn set_active_document(doc_id: Option<&str>) {
    let mut slot = ACTIVE_DOCUMENT_ID.lock().unwrap();
    *slot = doc_id.map(|s| s.to_string());
}

/// `set_active_model(model)` — set the current model name for version summaries.
pub fn set_active_model(model: Option<&str>) {
    let mut slot = ACTIVE_MODEL.lock().unwrap();
    *slot = model.map(|s| s.to_string());
}

/// `get_active_document()`.
pub fn get_active_document() -> Option<String> {
    ACTIVE_DOCUMENT_ID.lock().unwrap().clone()
}

/// `clear_active_document(doc_id=None) -> bool` — clear the in-memory
/// active-document pointer.
///
/// With `doc_id` given, only clears when it matches the current pointer, so a
/// different active document is left untouched. Returns `true` if it was cleared.
///
/// Called when a document is detached from its session or deleted (its tab is
/// closed): without this, the stale pointer makes the last-resort doc-injection
/// path re-surface a closed document in a later, unrelated chat — even one whose
/// session no longer matches — because an unlinked doc has `session_id` NULL
/// (#1160).
pub fn clear_active_document(doc_id: Option<&str>) -> bool {
    let mut slot = ACTIVE_DOCUMENT_ID.lock().unwrap();
    // if doc_id is None or _active_document_id == doc_id: clear; else leave.
    match doc_id {
        None => {
            *slot = None;
            true
        }
        Some(d) => {
            if slot.as_deref() == Some(d) {
                *slot = None;
                true
            } else {
                false
            }
        }
    }
}

/// Internal read of `_active_document_id` (Python reads the global directly in
/// several `do_*` bodies; there is no public getter for it in the source).
fn active_document_id() -> Option<String> {
    ACTIVE_DOCUMENT_ID.lock().unwrap().clone()
}

/// `_active_model or 'AI'` — the model name used in version summaries.
/// Python reads `_active_model` inline (no getter exists); this is the internal
/// accessor for that read.
fn active_model() -> Option<String> {
    ACTIVE_MODEL.lock().unwrap().clone()
}

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

// Known languages the editor understands (match the <select> in HTML). Used by
// `do_create_document` to validate / fall back. A `Lazy<HashSet>` mirroring the
// Python fn-local `_KNOWN_LANGS` set.
static KNOWN_LANGS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "python", "javascript", "typescript", "html", "css", "markdown", "json",
        "yaml", "bash", "sql", "rust", "go", "java", "c", "cpp", "xml", "toml",
        "ini", "ruby", "php", "csv", "email", "text", "plain", "svg",
    ]
    .into_iter()
    .collect()
});

// Regexes for _sniff_doc_language (line-anchored code signals).
static SNIFF_HTML_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"<(div|body|head|p|span|table|button|h[1-6]|ul|ol|li|img)\b").unwrap());
static SNIFF_PY_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?m)^\s*(def \w|class \w|import \w|from \w[\w.]* import )").unwrap());
static SNIFF_JS_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?m)^\s*(function \w|const \w|let \w|export |import .* from )").unwrap());
static SNIFF_SQL_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?mi)^\s*(select .* from |create table |insert into |update \w)").unwrap());
static SNIFF_CSS_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?m)^[.#]?[\w-]+\s*\{[^{}]*:[^{}]*;").unwrap());

// Email-header detection regexes (To:/Subject:), case-insensitive + multiline.
static EMAIL_TO_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?im)^To:\s*").unwrap());
static EMAIL_SUBJECT_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?im)^Subject:\s*").unwrap());
// Header-line prefix for _coerce_email_document_content (matches a trimmed line).
static EMAIL_HEADER_LINE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)^(To|Cc|Bcc|Subject|In-Reply-To|References|X-Source-UID|X-Source-Folder|X-Attachments):").unwrap()
});

// XML tag extraction for do_create_document (DOTALL + IGNORECASE).
static TITLE_TAG_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?is)<title>\s*(.*?)\s*</title>").unwrap());
static LANGUAGE_TAG_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?is)<language>\s*(.*?)\s*</language>").unwrap());
static CONTENT_TAG_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?is)<content>\s*(.*?)\s*</content>").unwrap());
// Strip any stray XML-ish doc tags during the line-based fallback.
static STRIP_DOC_TAGS_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"</?(?:title|language|content)>").unwrap());

// FIND/REPLACE and FIND/SUGGEST/REASON block patterns (DOTALL).
static EDIT_BLOCK_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?s)<<<FIND>>>\n(.*?)\n<<<REPLACE>>>\n(.*?)\n<<<END>>>").unwrap());
static SUGGEST_BLOCK_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?s)<<<FIND>>>\n(.*?)\n<<<SUGGEST>>>\n(.*?)\n<<<REASON>>>\n(.*?)\n<<<END>>>").unwrap()
});
// Leading "<digits><tab>" line-number gutter stripped from each FIND line.
static LINE_GUTTER_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^\d+\t").unwrap());

/// `_sniff_doc_language(text)` — best-effort detect a document's language from
/// its content when the model didn't specify one. Defaults to `'markdown'`
/// (prose). Recognizes the common markup/code types the editor supports so e.g.
/// an SVG isn't saved as markdown.
pub fn _sniff_doc_language(text: &str) -> String {
    // s = (text or "").strip()
    let s = text.trim();
    if s.is_empty() {
        return "markdown".to_string();
    }
    // head = s[:600]; hl = head.lower()  (char-sliced)
    let head: String = s.chars().take(600).collect();
    let hl = head.to_lowercase();

    if _looks_like_email_document(s, "") {
        return "email".to_string();
    }
    // Markup (unambiguous)
    if hl.contains("<svg") {
        return "svg".to_string();
    }
    if hl.starts_with("<?xml") {
        return "xml".to_string();
    }
    if hl.starts_with("<!doctype html")
        || hl.starts_with("<html")
        || SNIFF_HTML_RE.is_match(&hl)
    {
        return "html".to_string();
    }
    // JSON — if s[0] in "{[" try to parse.
    if let Some(first_char) = s.chars().next() {
        if (first_char == '{' || first_char == '[') && serde_json::from_str::<Value>(s).is_ok() {
            return "json".to_string();
        }
    }
    // Shebang — first = s.split("\n", 1)[0].strip().lower()
    let first = s.split('\n').next().unwrap_or("").trim().to_lowercase();
    if first.starts_with("#!") {
        return if first.contains("python") { "python" } else { "bash" }.to_string();
    }
    // Code by strong leading signals (line-anchored).
    if SNIFF_PY_RE.is_match(s) {
        return "python".to_string();
    }
    if SNIFF_JS_RE.is_match(s) {
        return "javascript".to_string();
    }
    if SNIFF_SQL_RE.is_match(s) {
        return "sql".to_string();
    }
    if SNIFF_CSS_RE.is_match(s) {
        return "css".to_string();
    }
    "markdown".to_string()
}

/// `_looks_like_email_document(text="", title="")`.
pub fn _looks_like_email_document(text: &str, title: &str) -> bool {
    // title_l = (title or "").strip().lower()
    let title_l = title.trim().to_lowercase();
    if matches!(title_l.as_str(), "new email" | "new mail" | "new message") {
        return true;
    }
    // s = (text or "").lstrip()
    let s = text.trim_start();
    if s.contains("\n---\n") && EMAIL_TO_RE.is_match(s) && EMAIL_SUBJECT_RE.is_match(s) {
        return true;
    }
    EMAIL_TO_RE.is_match(s) && EMAIL_SUBJECT_RE.is_match(s)
}

/// `_coerce_email_document_content(existing, incoming)` — keep email docs in the
/// `To/Subject/---/body` shape even if a model writes only the body or dumps
/// header labels without the separator.
pub fn _coerce_email_document_content(existing: &str, incoming: &str) -> String {
    // old = existing or ""; new = (incoming or "").strip()
    let old = existing;
    let new = incoming.trim();
    if new.contains("\n---\n") {
        return new.to_string();
    }
    // header = old.split("\n---\n", 1)[0] if "\n---\n" in old else "To: \nSubject: "
    let header: String = if old.contains("\n---\n") {
        old.split("\n---\n").next().unwrap_or("").to_string()
    } else {
        "To: \nSubject: ".to_string()
    };

    let body: String = if _looks_like_email_document(new, "") {
        // lines = new.splitlines(); find last header line; body = lines after it.
        let lines: Vec<&str> = new.split('\n').collect();
        let mut last_header_idx: isize = -1;
        for (i, line) in lines.iter().enumerate() {
            if EMAIL_HEADER_LINE_RE.is_match(line.trim()) {
                last_header_idx = i as isize;
            }
        }
        // body_lines = lines[last_header_idx + 1:] if last_header_idx >= 0 else lines
        let mut body_lines: Vec<&str> = if last_header_idx >= 0 {
            lines[(last_header_idx as usize + 1)..].to_vec()
        } else {
            lines.clone()
        };
        // while body_lines and not body_lines[0].strip(): body_lines.pop(0)
        while !body_lines.is_empty() && body_lines[0].trim().is_empty() {
            body_lines.remove(0);
        }
        body_lines.join("\n").trim().to_string()
    } else {
        new.to_string()
    };

    // return header.rstrip() + "\n---\n" + body
    format!("{}\n---\n{}", header.trim_end(), body)
}

/// `parse_edit_blocks(content)` — parse `<<<FIND>>>...<<<REPLACE>>>...<<<END>>>`
/// blocks into `[{"find": .., "replace": ..}, ...]`.
pub fn parse_edit_blocks(content: &str) -> Vec<Map<String, Value>> {
    let mut edits = Vec::new();
    for caps in EDIT_BLOCK_RE.captures_iter(content) {
        let mut m = Map::new();
        m.insert(
            "find".to_string(),
            Value::String(caps.get(1).map_or("", |x| x.as_str()).to_string()),
        );
        m.insert(
            "replace".to_string(),
            Value::String(caps.get(2).map_or("", |x| x.as_str()).to_string()),
        );
        edits.push(m);
    }
    edits
}

/// `parse_suggest_blocks(content)` — parse
/// `<<<FIND>>>...<<<SUGGEST>>>...<<<REASON>>>...<<<END>>>` blocks, skipping no-op
/// suggestions (find == replace, or a reason that says "no change").
pub fn parse_suggest_blocks(content: &str) -> Vec<Map<String, Value>> {
    let mut suggestions: Vec<Map<String, Value>> = Vec::new();
    const SKIP_PHRASES: [&str; 6] = [
        "no change", "clear", "fine as", "looks good", "no improvement", "keep as",
    ];
    for caps in SUGGEST_BLOCK_RE.captures_iter(content) {
        let find_text = caps.get(1).map_or("", |x| x.as_str());
        let replace_text = caps.get(2).map_or("", |x| x.as_str());
        let reason = caps.get(3).map_or("", |x| x.as_str()).trim();
        // Skip no-op suggestions where find == replace or reason says no change.
        if find_text.trim() == replace_text.trim() {
            continue;
        }
        let reason_lower = reason.to_lowercase();
        if SKIP_PHRASES.iter().any(|p| reason_lower.contains(p)) {
            continue;
        }
        let mut m = Map::new();
        m.insert(
            "id".to_string(),
            Value::String(format!("sugg-{}", suggestions.len() + 1)),
        );
        m.insert("find".to_string(), Value::String(find_text.to_string()));
        m.insert("replace".to_string(), Value::String(replace_text.to_string()));
        m.insert("reason".to_string(), Value::String(reason.to_string()));
        suggestions.push(m);
    }
    suggestions
}

// ---------------------------------------------------------------------------
// Small error/result helpers local to this module
// ---------------------------------------------------------------------------

/// `{"error": <msg>}` — the bare error shape the create/update/edit/suggest tools
/// return (NO `exit_code`, unlike `err_result`).
fn err_only(msg: impl Into<String>) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert("error".to_string(), Value::String(msg.into()));
    m
}

// ---------------------------------------------------------------------------
// do_create_document  (src/tool_implementations.py L174-286)
// ---------------------------------------------------------------------------

/// `do_create_document(content_block, session_id=None)` — create a new document.
///
/// Supports two formats:
///   1. Line-based: line 1 = title, line 2 (optional) = language, rest = content
///   2. XML-like tags: `<title>..</title><language>..</language><content>..</content>`
///
/// Some models mix them — strip any XML-style tags and fall back to line parsing.
pub async fn do_create_document(
    content_block: &str,
    session_id: Option<&str>,
    owner: Option<&str>,
) -> Map<String, Value> {
    let raw = content_block;

    // Try XML tag extraction first.
    let mut title: Option<String> = None;
    let mut language: Option<String> = None;
    let mut content: Option<String> = None;
    let mt = TITLE_TAG_RE.captures(raw);
    let ml = LANGUAGE_TAG_RE.captures(raw);
    let mc = CONTENT_TAG_RE.captures(raw);
    if mt.is_some() || mc.is_some() {
        title = mt.map(|c| c.get(1).map_or("", |x| x.as_str()).trim().to_string());
        language = ml.map(|c| c.get(1).map_or("", |x| x.as_str()).trim().to_lowercase());
        content = mc.map(|c| c.get(1).map_or("", |x| x.as_str()).to_string());
    }

    // Fall back to line-based parsing. First strip any stray XML-ish tags.
    if title.is_none() || content.is_none() {
        let cleaned = STRIP_DOC_TAGS_RE.replace_all(raw, "");
        // lines = cleaned.strip().split("\n")
        let mut lines: Vec<String> = cleaned.trim().split('\n').map(|s| s.to_string()).collect();
        if title.is_none() {
            // title = lines[0].strip() if lines else "Untitled"; lines = lines[1:]
            // Note: `cleaned.trim().split('\n')` is never empty in Rust (an empty
            // string yields [""]) — Python's `"".split("\n")` is `[""]` too, so the
            // `if lines` is always truthy here; `lines[0]` is "" for empty input.
            title = Some(lines.first().map(|l| l.trim().to_string()).unwrap_or_else(|| "Untitled".to_string()));
            if !lines.is_empty() {
                lines.remove(0);
            }
        }
        // Only consume the second line as language if it looks like a valid short
        // lang token.
        if language.is_none() && !lines.is_empty() {
            let candidate = lines[0].trim().to_lowercase();
            if !candidate.is_empty()
                && candidate.chars().count() < 20
                && !candidate.contains(' ')
                && KNOWN_LANGS.contains(candidate.as_str())
            {
                language = Some(candidate);
                lines.remove(0);
            }
        }
        if content.is_none() {
            content = Some(lines.join("\n"));
        }
    }

    let mut title = title.unwrap_or_default();
    let content = content.unwrap_or_default();

    // Validate language: must be in the known set, else default based on content.
    if let Some(lang) = &language {
        if !KNOWN_LANGS.contains(lang.as_str()) {
            language = None;
        }
    }
    let mut language: String = match language {
        Some(l) if !l.is_empty() => l,
        // No explicit language — sniff it from the content.
        _ => _sniff_doc_language(&content),
    };
    if _looks_like_email_document(&content, &title) {
        language = "email".to_string();
    }

    if title.is_empty() {
        title = "Untitled".to_string();
    }

    let session_id = match session_id {
        Some(s) if !s.is_empty() => s,
        _ => return err_only("No session context for document creation"),
    };

    // DB body — Python try/except db: ... rollback -> {"error": ...}.
    let result: rusqlite::Result<Map<String, Value>> = (|| {
        let conn = session_local()?;
        let doc_id = uuid::Uuid::new_v4().to_string();
        let ver_id = uuid::Uuid::new_v4().to_string();

        // Inherit ownership from the chat session (so the doc survives that session
        // later being deleted -> session_id NULL).
        //
        // Python: `_sess = db.query(DbSession).filter(...).first()`. We must keep the
        // distinction between "no session row" and "row with NULL owner" — the
        // ownership check below treats a MISSING session as a refusal when the
        // caller is scoped, whereas a found session with NULL owner is not. So fetch
        // the whole row (as an `Option`) rather than `flatten`-ing it away.
        let sess: Option<Option<String>> = conn
            .query_row(
                "SELECT owner FROM sessions WHERE id = ?1",
                [session_id],
                |r| r.get::<_, Option<String>>(0),
            )
            .optional()?;

        // if owner is not None and (not _sess or _sess.owner != owner): error
        if let Some(caller) = owner {
            let sess_owner = sess.clone().flatten();
            if sess.is_none() || sess_owner.as_deref() != Some(caller) {
                return Ok(err_only("Cannot create document in another user's session"));
            }
        }
        // _owner = _sess.owner if _sess else None
        let owner: Option<String> = sess.flatten();

        let now = crate::pydatetime::utcnow_naive_iso();
        conn.execute(
            "INSERT INTO documents \
               (id, session_id, title, language, current_content, version_count, \
                is_active, archived, owner, created_at, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, 1, 1, 0, ?6, ?7, ?7)",
            rusqlite::params![doc_id, session_id, title, language, content, owner, now],
        )?;
        let summary = format!("Created by {}", active_model().unwrap_or_else(|| "AI".to_string()));
        conn.execute(
            "INSERT INTO document_versions \
               (id, document_id, version_number, content, summary, source, created_at) \
             VALUES (?1, ?2, 1, ?3, ?4, 'ai', ?5)",
            rusqlite::params![ver_id, doc_id, content, summary, now],
        )?;

        set_active_document(Some(&doc_id));
        // fire_event("document_created", _owner) — Python wraps this in
        // try/except and only logs.debug on failure; `fire_event` swallows its
        // own errors (spawn/block_on), so a bare call matches that behavior
        // (it internally no-ops when no subscriber is registered).
        crate::src::event_bus::fire_event("document_created", owner.as_deref());

        let mut m = Map::new();
        m.insert("action".to_string(), Value::String("create".to_string()));
        m.insert("doc_id".to_string(), Value::String(doc_id));
        m.insert("title".to_string(), Value::String(title));
        m.insert("language".to_string(), Value::String(language));
        m.insert("content".to_string(), Value::String(content));
        m.insert("version".to_string(), Value::from(1_i64));
        Ok(m)
    })();

    match result {
        Ok(m) => m,
        Err(e) => err_only(format!("Failed to create document: {e}")),
    }
}

// ---------------------------------------------------------------------------
// do_update_document  (src/tool_implementations.py L289-341)
// ---------------------------------------------------------------------------

/// `do_update_document(content, doc_id=None)` — update an existing document.
/// `content` = full new document text.
pub async fn do_update_document(
    content: &str,
    doc_id: Option<&str>,
    owner: Option<&str>,
) -> Map<String, Value> {
    // target_id = doc_id or _active_document_id
    let mut target_id: Option<String> = doc_id
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string())
        .or_else(active_document_id);

    let result: rusqlite::Result<Map<String, Value>> = (|| {
        let conn = session_local()?;
        // doc = owner-scoped first by id, else most-recently-updated owned doc.
        let mut doc = match &target_id {
            Some(tid) => fetch_doc_owned(&conn, tid, owner, false)?,
            None => None,
        };
        if doc.is_none() {
            doc = fetch_doc_most_recent_owned(&conn, owner, false)?;
            if let Some(d) = &doc {
                target_id = Some(d.id.clone());
                set_active_document(Some(&d.id));
                logger::info(&format!(
                    "update_document: fell back to most recent doc id={}",
                    d.id
                ));
            }
        }
        let doc = match doc {
            Some(d) => d,
            None => return Ok(err_only("No documents exist to update")),
        };
        let target_id = target_id.clone().unwrap_or_else(|| doc.id.clone());

        let cur = doc.current_content.clone().unwrap_or_default();
        let is_email_doc = doc.language.as_deref() == Some("email")
            || _looks_like_email_document(&cur, doc.title.as_deref().unwrap_or(""));
        let new_content = if is_email_doc {
            _coerce_email_document_content(&cur, content)
        } else {
            content.trim().to_string()
        };
        let new_language = if is_email_doc {
            "email".to_string()
        } else {
            doc.language.clone().unwrap_or_default()
        };

        let new_ver = doc.version_count + 1;
        let now = crate::pydatetime::utcnow_naive_iso();
        let summary = format!("Updated by {}", active_model().unwrap_or_else(|| "AI".to_string()));
        conn.execute(
            "INSERT INTO document_versions \
               (id, document_id, version_number, content, summary, source, created_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, 'ai', ?6)",
            rusqlite::params![
                uuid::Uuid::new_v4().to_string(),
                target_id,
                new_ver,
                new_content,
                summary,
                now
            ],
        )?;
        // doc.current_content / version_count (+ language when email) and updated_at.
        conn.execute(
            "UPDATE documents SET current_content = ?1, version_count = ?2, \
               language = ?3, updated_at = ?4 WHERE id = ?5",
            rusqlite::params![new_content, new_ver, new_language, now, target_id],
        )?;

        let mut m = Map::new();
        m.insert("action".to_string(), Value::String("update".to_string()));
        m.insert("doc_id".to_string(), Value::String(target_id));
        m.insert(
            "title".to_string(),
            doc.title.map(Value::String).unwrap_or(Value::Null),
        );
        m.insert("language".to_string(), Value::String(new_language));
        m.insert("content".to_string(), Value::String(new_content));
        m.insert("version".to_string(), Value::from(new_ver));
        Ok(m)
    })();

    match result {
        Ok(m) => m,
        Err(e) => err_only(format!("Failed to update document: {e}")),
    }
}

// ---------------------------------------------------------------------------
// do_edit_document  (src/tool_implementations.py L353-435)
// ---------------------------------------------------------------------------

/// `do_edit_document(content, doc_id=None)` — apply targeted FIND/REPLACE edits.
pub async fn do_edit_document(
    content: &str,
    doc_id: Option<&str>,
    owner: Option<&str>,
) -> Map<String, Value> {
    let mut target_id: Option<String> = doc_id
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string())
        .or_else(active_document_id);

    let edits = parse_edit_blocks(content);
    if edits.is_empty() {
        return err_only("No valid <<<FIND>>>...<<<REPLACE>>>...<<<END>>> blocks found");
    }

    let result: rusqlite::Result<Map<String, Value>> = (|| {
        let conn = session_local()?;
        let mut doc = match &target_id {
            Some(tid) => fetch_doc_owned(&conn, tid, owner, false)?,
            None => None,
        };
        if doc.is_none() {
            // Fallback: most recently updated owned document.
            doc = fetch_doc_most_recent_owned(&conn, owner, false)?;
            if let Some(d) = &doc {
                target_id = Some(d.id.clone());
                set_active_document(Some(&d.id));
                logger::info(&format!(
                    "edit_document: fell back to most recent doc id={} title={:?}",
                    d.id,
                    d.title.clone().unwrap_or_default()
                ));
            }
        }
        let doc = match doc {
            Some(d) => d,
            None => return Ok(err_only("No documents exist to edit")),
        };
        let target_id = target_id.clone().unwrap_or_else(|| doc.id.clone());

        let mut updated_content = doc.current_content.clone().unwrap_or_default();
        let mut applied = 0_i64;
        let mut skipped = 0_i64;
        for edit in &edits {
            let find = edit.get("find").and_then(|v| v.as_str()).unwrap_or("");
            let replace = edit.get("replace").and_then(|v| v.as_str()).unwrap_or("");
            if updated_content.contains(find) {
                updated_content = replace_first(&updated_content, find, replace);
                applied += 1;
            } else {
                // Defensive: strip a leading "<digits><tab>" from each FIND line and
                // retry — but only if the stripped form actually matches.
                let stripped: String = find
                    .split('\n')
                    .map(|l| LINE_GUTTER_RE.replace(l, "").into_owned())
                    .collect::<Vec<_>>()
                    .join("\n");
                if stripped != find && updated_content.contains(&stripped) {
                    updated_content = replace_first(&updated_content, &stripped, replace);
                    applied += 1;
                    logger::info(
                        "edit_document: matched after stripping line-number gutter from FIND",
                    );
                } else {
                    let snippet: String = find.chars().take(80).collect();
                    logger::warning(&format!(
                        "edit_document: FIND text not found, skipping: {:?}",
                        snippet
                    ));
                    skipped += 1;
                }
            }
        }

        if applied == 0 {
            return Ok(err_only(format!(
                "No edits applied — none of the FIND blocks matched the document content (skipped {skipped})"
            )));
        }

        let new_ver = doc.version_count + 1;
        let now = crate::pydatetime::utcnow_naive_iso();
        let summary = format!(
            "Edited by {} ({} edit(s))",
            active_model().unwrap_or_else(|| "AI".to_string()),
            applied
        );
        conn.execute(
            "INSERT INTO document_versions \
               (id, document_id, version_number, content, summary, source, created_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, 'ai', ?6)",
            rusqlite::params![
                uuid::Uuid::new_v4().to_string(),
                target_id,
                new_ver,
                updated_content,
                summary,
                now
            ],
        )?;
        conn.execute(
            "UPDATE documents SET current_content = ?1, version_count = ?2, updated_at = ?3 \
             WHERE id = ?4",
            rusqlite::params![updated_content, new_ver, now, target_id],
        )?;

        let mut m = Map::new();
        m.insert("action".to_string(), Value::String("edit".to_string()));
        m.insert("doc_id".to_string(), Value::String(target_id));
        m.insert(
            "title".to_string(),
            doc.title.map(Value::String).unwrap_or(Value::Null),
        );
        m.insert(
            "language".to_string(),
            doc.language.map(Value::String).unwrap_or(Value::Null),
        );
        m.insert("content".to_string(), Value::String(updated_content));
        m.insert("version".to_string(), Value::from(new_ver));
        m.insert("applied".to_string(), Value::from(applied));
        m.insert("skipped".to_string(), Value::from(skipped));
        Ok(m)
    })();

    match result {
        Ok(m) => m,
        Err(e) => err_only(format!("Failed to edit document: {e}")),
    }
}

// ---------------------------------------------------------------------------
// do_suggest_document  (src/tool_implementations.py L461-497)
// ---------------------------------------------------------------------------

/// `do_suggest_document(content, doc_id=None)` — create inline suggestions for the
/// active document WITHOUT modifying it.
pub async fn do_suggest_document(
    content: &str,
    doc_id: Option<&str>,
    owner: Option<&str>,
) -> Map<String, Value> {
    // target_id = doc_id or _active_document_id
    let target_id: Option<String> = doc_id
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string())
        .or_else(active_document_id);
    let target_id = match target_id {
        Some(t) => t,
        None => return err_only("No active document to suggest on"),
    };

    let suggestions = parse_suggest_blocks(content);
    if suggestions.is_empty() {
        return err_only(
            "No valid <<<FIND>>>...<<<SUGGEST>>>...<<<REASON>>>...<<<END>>> blocks found",
        );
    }

    let result: rusqlite::Result<Map<String, Value>> = (|| {
        let conn = session_local()?;
        let doc = fetch_doc_owned(&conn, &target_id, owner, false)?;
        let doc = match doc {
            Some(d) => d,
            None => return Ok(err_only(format!("Document {target_id} not found"))),
        };
        let cur = doc.current_content.clone().unwrap_or_default();

        // Validate that FIND text exists in the document.
        let mut valid: Vec<Map<String, Value>> = Vec::new();
        for s in suggestions {
            let find = s.get("find").and_then(|v| v.as_str()).unwrap_or("");
            if cur.contains(find) {
                valid.push(s);
            } else {
                let snippet: String = find.chars().take(80).collect();
                logger::warning(&format!(
                    "suggest_document: FIND text not found, skipping: {:?}",
                    snippet
                ));
            }
        }

        if valid.is_empty() {
            return Ok(err_only("No suggestions matched the document content"));
        }

        let count = valid.len() as i64;
        let mut m = Map::new();
        m.insert("action".to_string(), Value::String("suggest".to_string()));
        m.insert("doc_id".to_string(), Value::String(target_id));
        m.insert(
            "suggestions".to_string(),
            Value::Array(valid.into_iter().map(Value::Object).collect()),
        );
        m.insert("count".to_string(), Value::from(count));
        Ok(m)
    })();

    match result {
        Ok(m) => m,
        Err(e) => err_only(format!("Failed to suggest on document: {e}")),
    }
}

// ---------------------------------------------------------------------------
// do_manage_documents  (src/tool_implementations.py L1336-1450)
// ---------------------------------------------------------------------------

/// `do_manage_documents(content, owner=None)` — manage documents: list,
/// read/view/open/get, delete, tidy.
///
/// Output format mirrors `manage_session`: list rows include a clickable
/// `[Title](#document-<id>)` anchor + relative timestamps so the user can click
/// straight from chat to open the editor.
pub async fn do_manage_documents(content: &str, owner: Option<&str>) -> Map<String, Value> {
    // args = _parse_tool_args(content) — ValueError -> {"error": .., "exit_code": 1}.
    // `_parse_tool_args` lives in the group root (`mod.rs`) and takes `&str`,
    // matching the sibling groups' usage; we reference it via `super::`.
    let args = match super::_parse_tool_args(content) {
        Ok(a) => a,
        Err(_) => {
            return super::err_result("Invalid JSON arguments");
        }
    };

    let action = args
        .get("action")
        .and_then(|v| v.as_str())
        .unwrap_or("list")
        .to_string();

    // tidy is async (`run_document_tidy` awaits) so it cannot live inside the
    // sync rusqlite closure below — handle it up front.
    // Python (tool_implementations.py:1439-1442):
    //   result = await run_document_tidy(owner or "")
    //   return {"response": result, "exit_code": 0}
    // A raised TaskNoop (no junk) is caught by the broad `except Exception as e`
    // -> {"error": str(e), "exit_code": 1}; here `Err(Outcome::Noop(..))` is the
    // same sentinel, surfaced via `err_result(o.reason())`.
    if action == "tidy" {
        return match crate::src::document_actions::run_document_tidy(owner.unwrap_or(""))
            .await
        {
            Ok((summary, _)) => {
                let mut m = Map::new();
                m.insert("response".to_string(), Value::String(summary));
                m.insert("exit_code".to_string(), Value::from(0_i64));
                m
            }
            Err(o) => super::err_result(o.reason()),
        };
    }

    let result: rusqlite::Result<Map<String, Value>> = (|| {
        let conn = session_local()?;
        match action.as_str() {
            "list" => {
                // q = documents WHERE is_active == True [AND title ilike] [AND language ==]
                // ORDER BY updated_at DESC LIMIT limit
                let search = args.get("search").and_then(|v| v.as_str());
                let language = args.get("language").and_then(|v| v.as_str());
                let limit = args
                    .get("limit")
                    .and_then(|v| v.as_i64())
                    .unwrap_or(50);

                // Select the canonical `DOC_COLS` layout so the shared
                // `doc_from_row` (used by `query_docs`) reads each column from the
                // right index — `version_count`(4), `updated_at`(5), `created_at`(6).
                // A bespoke 6-column SELECT here silently shifted those fields and
                // made `doc_from_row` read a non-existent column index 6, turning
                // every `list` call into an error result with no `response` key.
                let mut sql = format!(
                    "SELECT {DOC_COLS} FROM documents WHERE is_active = 1"
                );
                let mut params: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();
                // _owned_document_query(q, Document, owner): when `owner` is None the
                // Python applies a SQL `false()` literal -> zero rows; otherwise it
                // filters `owner == owner`.
                match owner {
                    Some(o) => {
                        sql.push_str(" AND owner = ?");
                        params.push(Box::new(o.to_string()));
                    }
                    None => sql.push_str(" AND 0"),
                }
                if let Some(s) = search.filter(|s| !s.is_empty()) {
                    // Document.title.ilike(f"%{search}%") — no explicit escape in Python.
                    sql.push_str(" AND lower(title) LIKE ?");
                    params.push(Box::new(format!("%{}%", s.to_lowercase())));
                }
                if let Some(l) = language.filter(|l| !l.is_empty()) {
                    sql.push_str(" AND language = ?");
                    params.push(Box::new(l.to_string()));
                }
                sql.push_str(" ORDER BY updated_at DESC LIMIT ?");
                params.push(Box::new(limit));

                let docs = query_docs(&conn, &sql, &params)?;
                if docs.is_empty() {
                    // msg = "No documents found" + (" matching '<s>'" if search) + "."
                    let msg = match search.filter(|s| !s.is_empty()) {
                        Some(s) => format!("No documents found matching '{s}'."),
                        None => "No documents found.".to_string(),
                    };
                    let mut m = Map::new();
                    m.insert("response".to_string(), Value::String(msg));
                    m.insert("documents".to_string(), Value::Array(Vec::new()));
                    m.insert("exit_code".to_string(), Value::from(0_i64));
                    return Ok(m);
                }
                let mut lines: Vec<String> = Vec::new();
                let mut items: Vec<Value> = Vec::new();
                for (i, d) in docs.iter().enumerate() {
                    let size = d.current_content.as_deref().unwrap_or("").chars().count() as i64;
                    let lang = d.language.clone().unwrap_or_else(|| "text".to_string());
                    // ts = updated_at or created_at
                    let ts = d.updated_at.clone().or_else(|| d.created_at.clone());
                    let marker = if i == 0 { " ← most recent" } else { "" };
                    let title = d.title.clone().unwrap_or_default();
                    lines.push(format!(
                        "- [{title}](#document-{}) — {lang}, {size} chars, updated {}{marker}",
                        d.id,
                        rel_time(ts.as_deref())
                    ));
                    let mut item = Map::new();
                    item.insert("id".to_string(), Value::String(d.id.clone()));
                    item.insert("title".to_string(), Value::String(title));
                    item.insert("language".to_string(), Value::String(lang));
                    item.insert("size".to_string(), Value::from(size));
                    items.push(Value::Object(item));
                }
                let header = format!(
                    "Found {} document(s), sorted most-recent first. Click a title to open:",
                    docs.len()
                );
                let mut m = Map::new();
                m.insert(
                    "response".to_string(),
                    Value::String(format!("{header}\n{}", lines.join("\n"))),
                );
                m.insert("documents".to_string(), Value::Array(items));
                m.insert("exit_code".to_string(), Value::from(0_i64));
                Ok(m)
            }

            "read" | "view" | "open" | "get" => {
                let doc_id = args
                    .get("document_id")
                    .and_then(|v| v.as_str())
                    .or_else(|| args.get("id").and_then(|v| v.as_str()))
                    .or_else(|| args.get("uid").and_then(|v| v.as_str()));
                let doc_id = match doc_id.filter(|s| !s.is_empty()) {
                    Some(d) => d,
                    None => {
                        return Ok(super::err_result(
                            "Need document_id (use action=list to find one)",
                        ))
                    }
                };
                let doc = fetch_doc_owned(&conn, doc_id, owner, true)?;
                let doc = match doc {
                    Some(d) => d,
                    None => return Ok(super::err_result(format!("Document '{doc_id}' not found"))),
                };
                let body = doc.current_content.clone().unwrap_or_default();
                let body_len = body.chars().count() as i64;
                // preview_limit = int(args.get("limit", MAX_READ_CHARS))
                let preview_limit = args.get("limit").and_then(|v| v.as_i64()).unwrap_or(MAX_READ_CHARS);
                let truncated = body_len > preview_limit;
                let preview: String = if truncated {
                    let head: String = body.chars().take(preview_limit.max(0) as usize).collect();
                    format!("{head}\n... (truncated, {body_len} chars total)")
                } else {
                    body.clone()
                };
                let title = doc.title.clone().unwrap_or_default();
                let lang = doc.language.clone().unwrap_or_default();
                let anchor = format!("[{title}](#document-{})", doc.id);
                let mut m = Map::new();
                m.insert(
                    "response".to_string(),
                    Value::String(format!(
                        "{anchor} — click to open in editor.\n\n```{lang}\n{preview}\n```"
                    )),
                );
                let mut docmap = Map::new();
                docmap.insert("id".to_string(), Value::String(doc.id.clone()));
                docmap.insert(
                    "title".to_string(),
                    doc.title.map(Value::String).unwrap_or(Value::Null),
                );
                docmap.insert(
                    "language".to_string(),
                    doc.language.map(Value::String).unwrap_or(Value::Null),
                );
                docmap.insert("size".to_string(), Value::from(body_len));
                docmap.insert("content".to_string(), Value::String(preview));
                docmap.insert("truncated".to_string(), Value::Bool(truncated));
                m.insert("document".to_string(), Value::Object(docmap));
                m.insert("exit_code".to_string(), Value::from(0_i64));
                Ok(m)
            }

            "delete" => {
                // doc_id = document_id|id|uid|_active_document_id
                let active = active_document_id();
                let doc_id: Option<String> = args
                    .get("document_id")
                    .and_then(|v| v.as_str())
                    .or_else(|| args.get("id").and_then(|v| v.as_str()))
                    .or_else(|| args.get("uid").and_then(|v| v.as_str()))
                    .filter(|s| !s.is_empty())
                    .map(|s| s.to_string())
                    .or(active);

                let mut doc = match &doc_id {
                    Some(d) => fetch_doc_owned(&conn, d, owner, false)?,
                    None => None,
                };
                if doc.is_none() {
                    // Fallback: most recently updated active owned doc.
                    doc = fetch_doc_most_recent_owned(&conn, owner, true)?;
                }
                let doc = match doc {
                    Some(d) => d,
                    None => return Ok(super::err_result("No document to delete")),
                };
                let title = doc.title.clone().unwrap_or_default();
                conn.execute("UPDATE documents SET is_active = 0 WHERE id = ?1", [&doc.id])?;
                if active_document_id().as_deref() == Some(doc.id.as_str()) {
                    set_active_document(None);
                }
                let mut m = Map::new();
                m.insert(
                    "response".to_string(),
                    Value::String(format!("Deleted document '{title}'")),
                );
                m.insert("exit_code".to_string(), Value::from(0_i64));
                Ok(m)
            }

            // "tidy" is handled before this sync closure (it is async).
            other => Ok(super::err_result(format!("Unknown action: {other}"))),
        }
    })();

    match result {
        Ok(m) => m,
        Err(e) => {
            logger::error(&format!("manage_documents error: {e}"));
            super::err_result(e.to_string())
        }
    }
}

// ---------------------------------------------------------------------------
// Row reading helpers (raw-SQL analogues of the SQLAlchemy `Document` queries)
// ---------------------------------------------------------------------------

/// A subset of a `documents` row used by these tools.
struct DocRow {
    id: String,
    title: Option<String>,
    language: Option<String>,
    current_content: Option<String>,
    version_count: i64,
    updated_at: Option<String>,
    created_at: Option<String>,
}

const DOC_COLS: &str =
    "id, title, language, current_content, version_count, updated_at, created_at";

fn doc_from_row(r: &rusqlite::Row) -> rusqlite::Result<DocRow> {
    Ok(DocRow {
        id: r.get(0)?,
        title: r.get(1)?,
        language: r.get(2)?,
        current_content: r.get(3)?,
        version_count: r.get::<_, Option<i64>>(4)?.unwrap_or(1),
        updated_at: r.get(5)?,
        created_at: r.get(6)?,
    })
}

/// `_get_owned_document(db, Document, doc_id, owner, active_only=False)`.
///
/// Owner-scoped fetch of a single document by id. Mirrors
/// `_owned_document_query`: when `owner` is `None` the Python applies the SQL
/// `false()` literal, returning ZERO rows so an unscoped caller can never read
/// another tenant's document — here we short-circuit to `None` (the equivalent
/// of `WHERE false`, but skipping the query entirely).
fn fetch_doc_owned(
    conn: &rusqlite::Connection,
    id: &str,
    owner: Option<&str>,
    active_only: bool,
) -> rusqlite::Result<Option<DocRow>> {
    let owner = match owner {
        Some(o) => o,
        None => return Ok(None),
    };
    let mut sql = format!("SELECT {DOC_COLS} FROM documents WHERE id = ?1");
    if active_only {
        sql.push_str(" AND is_active = 1");
    }
    sql.push_str(" AND owner = ?2");
    conn.query_row(&sql, rusqlite::params![id, owner], doc_from_row)
        .optional()
}

/// `_most_recent_owned_document(db, Document, owner, active_only=False)`.
///
/// Owner-scoped most-recently-updated document. As with `fetch_doc_owned`, an
/// `owner` of `None` yields zero rows (the SQL `false()` literal).
fn fetch_doc_most_recent_owned(
    conn: &rusqlite::Connection,
    owner: Option<&str>,
    active_only: bool,
) -> rusqlite::Result<Option<DocRow>> {
    let owner = match owner {
        Some(o) => o,
        None => return Ok(None),
    };
    let mut sql = format!("SELECT {DOC_COLS} FROM documents WHERE owner = ?1");
    if active_only {
        sql.push_str(" AND is_active = 1");
    }
    sql.push_str(" ORDER BY updated_at DESC LIMIT 1");
    conn.query_row(&sql, rusqlite::params![owner], doc_from_row)
        .optional()
}

/// Run a list query that selects `DOC_COLS` with bound params.
fn query_docs(
    conn: &rusqlite::Connection,
    sql: &str,
    params: &[Box<dyn rusqlite::ToSql>],
) -> rusqlite::Result<Vec<DocRow>> {
    let mut stmt = conn.prepare(sql)?;
    let p: Vec<&dyn rusqlite::ToSql> = params.iter().map(|b| b.as_ref()).collect();
    let rows = stmt.query_map(p.as_slice(), doc_from_row)?;
    rows.collect()
}

use rusqlite::OptionalExtension;

// ---------------------------------------------------------------------------
// String helpers
// ---------------------------------------------------------------------------

/// Python `str.replace(find, replace, 1)` — replace only the first occurrence.
/// `str::replacen(.., 1)` is the direct analogue; an empty `find` matches at the
/// very start (Python inserts before index 0), which `replacen` reproduces.
fn replace_first(haystack: &str, find: &str, replace: &str) -> String {
    haystack.replacen(find, replace, 1)
}

/// `_rel(ts)` from `do_manage_documents` — render a relative timestamp.
///
/// The stored value is a naive-UTC datetime string (SQLAlchemy SQLite format), so
/// `ts.tzinfo is None` in the Python and it compares against `datetime.utcnow()`.
/// We parse the stored string, diff against `Utc::now().naive_utc()`, and format
/// the same buckets. Unparseable -> 'unknown'; missing -> 'never'.
fn rel_time(ts: Option<&str>) -> String {
    let ts = match ts {
        Some(s) if !s.is_empty() => s,
        _ => return "never".to_string(),
    };
    let parsed = chrono::NaiveDateTime::parse_from_str(ts, "%Y-%m-%d %H:%M:%S%.f")
        .or_else(|_| chrono::NaiveDateTime::parse_from_str(ts, "%Y-%m-%d %H:%M:%S"));
    let dt = match parsed {
        Ok(dt) => dt,
        Err(_) => return "unknown".to_string(),
    };
    let now = chrono::Utc::now().naive_utc();
    // diff = (now - ts).total_seconds()
    let diff = (now - dt).num_seconds();
    if diff < 60 {
        return "just now".to_string();
    }
    if diff < 3600 {
        return format!("{}m ago", diff / 60);
    }
    if diff < 86400 {
        return format!("{}h ago", diff / 3600);
    }
    if diff < 86400 * 7 {
        return format!("{}d ago", diff / 86400);
    }
    // ts.strftime('%Y-%m-%d')
    dt.format("%Y-%m-%d").to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sniff_email_before_markup() {
        // Email detection runs before SVG/HTML/etc.
        let s = "To: a@b.com\nSubject: hi\n---\nbody";
        assert_eq!(_sniff_doc_language(s), "email");
    }

    #[test]
    fn sniff_svg_html_json_code() {
        assert_eq!(_sniff_doc_language("<svg width=\"1\"></svg>"), "svg");
        assert_eq!(_sniff_doc_language("<?xml version=\"1.0\"?>"), "xml");
        assert_eq!(_sniff_doc_language("<html><body></body></html>"), "html");
        assert_eq!(_sniff_doc_language("{\"a\": 1}"), "json");
        assert_eq!(_sniff_doc_language("def foo():\n    pass"), "python");
        assert_eq!(_sniff_doc_language("const x = 1;"), "javascript");
        assert_eq!(_sniff_doc_language("SELECT a FROM t"), "sql");
        assert_eq!(_sniff_doc_language(".x { color: red; }"), "css");
        assert_eq!(_sniff_doc_language("just some prose"), "markdown");
        assert_eq!(_sniff_doc_language("   "), "markdown");
    }

    #[test]
    fn sniff_shebang() {
        assert_eq!(_sniff_doc_language("#!/usr/bin/env python3\nx=1"), "python");
        assert_eq!(_sniff_doc_language("#!/bin/sh\necho hi"), "bash");
    }

    #[test]
    fn email_doc_detection() {
        assert!(_looks_like_email_document("", "New Email"));
        assert!(_looks_like_email_document("To: a\nSubject: b", ""));
        assert!(!_looks_like_email_document("hello world", ""));
    }

    #[test]
    fn coerce_email_keeps_separator() {
        let out = _coerce_email_document_content("", "To: a\nSubject: b\n---\nbody");
        assert_eq!(out, "To: a\nSubject: b\n---\nbody");
    }

    #[test]
    fn coerce_email_rebuilds_from_body() {
        // body-only incoming, existing has a header.
        let existing = "To: x\nSubject: y\n---\nold body";
        let out = _coerce_email_document_content(existing, "brand new body");
        assert_eq!(out, "To: x\nSubject: y\n---\nbrand new body");
    }

    #[test]
    fn parse_edit_blocks_basic() {
        let c = "<<<FIND>>>\nfoo\n<<<REPLACE>>>\nbar\n<<<END>>>";
        let e = parse_edit_blocks(c);
        assert_eq!(e.len(), 1);
        assert_eq!(e[0].get("find").unwrap().as_str().unwrap(), "foo");
        assert_eq!(e[0].get("replace").unwrap().as_str().unwrap(), "bar");
    }

    #[test]
    fn parse_suggest_blocks_skips_noop() {
        // find == replace -> skipped.
        let c = "<<<FIND>>>\nsame\n<<<SUGGEST>>>\nsame\n<<<REASON>>>\nx\n<<<END>>>";
        assert!(parse_suggest_blocks(c).is_empty());
        // "no change" reason -> skipped.
        let c2 = "<<<FIND>>>\na\n<<<SUGGEST>>>\nb\n<<<REASON>>>\nNo change needed\n<<<END>>>";
        assert!(parse_suggest_blocks(c2).is_empty());
        // "clearer wording" WOULD be skipped: Python's substring check matches
        // "clear" inside "clearer" (faithful behavior). Use a reason free of any
        // skip phrase for the positive case.
        let c3 = "<<<FIND>>>\na\n<<<SUGGEST>>>\nb\n<<<REASON>>>\nmore precise term\n<<<END>>>";
        let s = parse_suggest_blocks(c3);
        assert_eq!(s.len(), 1);
        assert_eq!(s[0].get("id").unwrap().as_str().unwrap(), "sugg-1");
    }

    #[test]
    fn replace_first_only_once() {
        assert_eq!(replace_first("a a a", "a", "b"), "b a a");
    }

    // -----------------------------------------------------------------------
    // clear_active_document  (tool_implementations.py:91-106)
    // -----------------------------------------------------------------------

    #[test]
    fn clear_active_document_semantics() {
        // The active-doc pointer is process-global and shared with the DB-backed
        // tests below, so serialize on the same guard to keep the assertions
        // deterministic in the shared lib-test binary.
        let _held = DB_GUARD.lock().unwrap_or_else(|p| p.into_inner());
        // doc_id=None always clears and returns True.
        set_active_document(Some("doc-a"));
        assert!(clear_active_document(None));
        assert_eq!(get_active_document(), None);

        // doc_id matching the pointer clears and returns True.
        set_active_document(Some("doc-a"));
        assert!(clear_active_document(Some("doc-a")));
        assert_eq!(get_active_document(), None);

        // doc_id NOT matching leaves the pointer untouched and returns False.
        set_active_document(Some("doc-a"));
        assert!(!clear_active_document(Some("doc-b")));
        assert_eq!(get_active_document(), Some("doc-a".to_string()));

        // Clearing when already empty with a doc_id returns False (no match).
        set_active_document(None);
        assert!(!clear_active_document(Some("doc-a")));

        // ...but clearing-with-None when empty returns True (idempotent clear).
        assert!(clear_active_document(None));
    }

    // -----------------------------------------------------------------------
    // Owner-scoping (DB-backed)
    //
    // Mirrors `rag_vector`/`vector_store`: a module-static guard serializes the
    // DB-backed tests (the process-global `DATABASE_URL` is unsynchronized across
    // the shared lib-test binary) and a fresh temp DB is used per call — NEVER the
    // live `data/` dir.
    // -----------------------------------------------------------------------


    use crate::core::database::DB_TEST_LOCK as DB_GUARD;

    fn with_temp_db<F: FnOnce()>(f: F) {
        let _held = DB_GUARD.lock().unwrap_or_else(|p| p.into_inner());
        let dir = std::env::temp_dir();
        let unique = format!(
            "odysseus_documents_{}_{}.db",
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
        // Each DB test owns the process-global active-document pointer; reset it.
        set_active_document(None);
        f();
    }

    /// Insert a `documents` row directly (bypassing the tool) with the given owner.
    fn insert_doc(id: &str, owner: Option<&str>, content: &str, is_active: bool) {
        let conn = session_local().unwrap();
        let now = crate::pydatetime::utcnow_naive_iso();
        conn.execute(
            "INSERT INTO documents \
               (id, session_id, title, language, current_content, version_count, \
                is_active, archived, owner, created_at, updated_at) \
             VALUES (?1, NULL, ?2, 'markdown', ?3, 1, ?4, 0, ?5, ?6, ?6)",
            rusqlite::params![
                id,
                format!("Title {id}"),
                content,
                if is_active { 1_i64 } else { 0_i64 },
                owner,
                now
            ],
        )
        .unwrap();
    }

    fn insert_session(id: &str, owner: Option<&str>) {
        let conn = session_local().unwrap();
        let now = crate::pydatetime::utcnow_naive_iso();
        conn.execute(
            "INSERT INTO sessions (id, name, endpoint_url, model, owner, created_at, updated_at) \
             VALUES (?1, 'S', '', 'm', ?2, ?3, ?3)",
            rusqlite::params![id, owner, now],
        )
        .unwrap();
    }

    #[test]
    fn fetch_doc_owned_filters_by_owner() {
        with_temp_db(|| {
            insert_doc("d1", Some("alice"), "hi", true);
            let conn = session_local().unwrap();
            // Same owner -> found.
            assert!(fetch_doc_owned(&conn, "d1", Some("alice"), false)
                .unwrap()
                .is_some());
            // Different owner -> None (cannot cross tenant).
            assert!(fetch_doc_owned(&conn, "d1", Some("bob"), false)
                .unwrap()
                .is_none());
            // owner=None -> zero rows (the SQL `false()` literal).
            assert!(fetch_doc_owned(&conn, "d1", None, false).unwrap().is_none());
        });
    }

    #[test]
    fn fetch_doc_most_recent_owned_filters_and_active() {
        with_temp_db(|| {
            insert_doc("d1", Some("alice"), "a", true);
            insert_doc("d2", Some("bob"), "b", true);
            insert_doc("d3", Some("alice"), "c", false); // inactive
            let conn = session_local().unwrap();
            // alice (any active) -> the active alice doc, never bob's.
            let r = fetch_doc_most_recent_owned(&conn, Some("alice"), true)
                .unwrap()
                .unwrap();
            assert_eq!(r.id, "d1");
            // owner=None -> nothing.
            assert!(fetch_doc_most_recent_owned(&conn, None, true)
                .unwrap()
                .is_none());
            // A tenant that owns nothing (carol) sees no document at all.
            assert!(fetch_doc_most_recent_owned(&conn, Some("carol"), false)
                .unwrap()
                .is_none());
        });
    }

    /// Acquire the DB guard, point `DATABASE_URL` at a fresh temp DB, run
    /// `create_all`, reset the active-doc pointer, and return both the held guard
    /// and a `TmpDb` cleanup handle. For the `#[tokio::test]` cases we cannot use
    /// the `with_temp_db(closure)` form (a closure may not span `.await`), so they
    /// set up via this helper and keep the returned guards alive for the test body.
    struct DbScope {
        _held: std::sync::MutexGuard<'static, ()>,
        _tmp: TmpDbFile,
    }
    struct TmpDbFile(std::path::PathBuf);
    impl Drop for TmpDbFile {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }

    fn db_scope(tag: &str) -> DbScope {
        let held = DB_GUARD.lock().unwrap_or_else(|p| p.into_inner());
        let path = std::env::temp_dir().join(format!(
            "odysseus_documents_{tag}_{}_{}.db",
            std::process::id(),
            uuid::Uuid::new_v4().simple()
        ));
        let _ = std::fs::remove_file(&path);
        std::env::set_var("DATABASE_URL", format!("sqlite:///{}", path.display()));
        crate::core::database::create_all().unwrap();
        set_active_document(None);
        DbScope {
            _held: held,
            _tmp: TmpDbFile(path),
        }
    }

    #[tokio::test]
    async fn manage_documents_list_is_owner_scoped() {
        let _scope = db_scope("list");
        insert_doc("d1", Some("alice"), "x", true);
        insert_doc("d2", Some("bob"), "y", true);

        let alice = do_manage_documents("{\"action\": \"list\"}", Some("alice")).await;
        let resp = alice.get("response").and_then(|v| v.as_str()).unwrap();
        assert!(resp.contains("#document-d1"), "alice should see her doc");
        assert!(!resp.contains("#document-d2"), "alice must NOT see bob's doc");

        // owner=None -> zero rows (no tenant), "No documents found."
        let anon = do_manage_documents("{\"action\": \"list\"}", None).await;
        assert_eq!(
            anon.get("response").and_then(|v| v.as_str()).unwrap(),
            "No documents found."
        );
    }

    #[tokio::test]
    async fn manage_documents_read_and_delete_owner_scoped() {
        let _scope = db_scope("rd");
        insert_doc("d1", Some("alice"), "secret", true);

        // bob cannot read alice's doc -> "not found".
        let r = do_manage_documents(
            "{\"action\": \"read\", \"document_id\": \"d1\"}",
            Some("bob"),
        )
        .await;
        assert_eq!(r.get("exit_code").and_then(|v| v.as_i64()), Some(1));
        assert_eq!(
            r.get("error").and_then(|v| v.as_str()),
            Some("Document 'd1' not found")
        );

        // bob cannot delete alice's doc -> "No document to delete" (fallback also
        // owner-scoped, so finds nothing for bob).
        let d = do_manage_documents(
            "{\"action\": \"delete\", \"document_id\": \"d1\"}",
            Some("bob"),
        )
        .await;
        assert_eq!(d.get("error").and_then(|v| v.as_str()), Some("No document to delete"));

        // alice can read it.
        let ar = do_manage_documents(
            "{\"action\": \"read\", \"document_id\": \"d1\"}",
            Some("alice"),
        )
        .await;
        assert_eq!(ar.get("exit_code").and_then(|v| v.as_i64()), Some(0));
    }

    #[tokio::test]
    async fn update_edit_suggest_are_owner_scoped() {
        let _scope = db_scope("ues");
        insert_doc("d1", Some("alice"), "hello world", true);

        // bob updating by id finds no owned doc and no owned fallback -> error.
        let u = do_update_document("new text", Some("d1"), Some("bob")).await;
        assert_eq!(u.get("error").and_then(|v| v.as_str()), Some("No documents exist to update"));

        // bob editing -> same.
        let e = do_edit_document(
            "<<<FIND>>>\nhello\n<<<REPLACE>>>\nhi\n<<<END>>>",
            Some("d1"),
            Some("bob"),
        )
        .await;
        assert_eq!(e.get("error").and_then(|v| v.as_str()), Some("No documents exist to edit"));

        // bob suggesting on alice's doc -> "Document d1 not found".
        let s = do_suggest_document(
            "<<<FIND>>>\nhello\n<<<SUGGEST>>>\nhi\n<<<REASON>>>\nbetter\n<<<END>>>",
            Some("d1"),
            Some("bob"),
        )
        .await;
        assert_eq!(s.get("error").and_then(|v| v.as_str()), Some("Document d1 not found"));

        // alice can update her own doc.
        let ok = do_update_document("brand new", Some("d1"), Some("alice")).await;
        assert_eq!(ok.get("action").and_then(|v| v.as_str()), Some("update"));
        assert_eq!(ok.get("version").and_then(|v| v.as_i64()), Some(2));
    }

    #[tokio::test]
    async fn create_document_refuses_other_users_session() {
        let _scope = db_scope("create");
        insert_session("s1", Some("alice"));

        // bob creating a doc in alice's session -> refused (bare error, no exit_code).
        let r = do_create_document("Title\nbody", Some("s1"), Some("bob")).await;
        assert_eq!(
            r.get("error").and_then(|v| v.as_str()),
            Some("Cannot create document in another user's session")
        );
        assert!(r.get("exit_code").is_none());

        // alice creating in her own session -> succeeds, inherits owner alice.
        let ok = do_create_document("Title\nbody", Some("s1"), Some("alice")).await;
        assert_eq!(ok.get("action").and_then(|v| v.as_str()), Some("create"));
        let doc_id = ok.get("doc_id").and_then(|v| v.as_str()).unwrap().to_string();
        {
            let conn = session_local().unwrap();
            let owner: Option<String> = conn
                .query_row(
                    "SELECT owner FROM documents WHERE id = ?1",
                    [&doc_id],
                    |r| r.get(0),
                )
                .unwrap();
            assert_eq!(owner.as_deref(), Some("alice"));
        }

        // owner=None caller with an existing (alice-owned) session is also refused
        // ONLY when scoped — here owner is None so the check is skipped and the doc
        // inherits the session owner (parity with Python's `if owner is not None`).
        let anon = do_create_document("Title\nbody2", Some("s1"), None).await;
        assert_eq!(anon.get("action").and_then(|v| v.as_str()), Some("create"));
    }
}
