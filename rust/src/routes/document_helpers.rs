// routes/document_helpers.rs  <- routes/document_helpers.py
//! document_helpers.py — Pydantic models, doc serializers, owner gating, and
//! file-locator helpers shared with document_routes.py.
//!
//! Pure-helper module (no router). The Python source extracts these symbols from
//! `document_routes.py` so the routes module can import them:
//!
//! * Request schemas: [`DocumentCreate`], [`DocumentUpdate`], [`DocumentPatch`]
//!   (pydantic `BaseModel`s → `#[derive(Deserialize)]` with `#[serde(default)]`
//!   reproducing each field's default / `Optional`).
//! * Serializers: [`doc_to_dict`] (`_doc_to_dict`, `isoformat()+"Z"`) and
//!   [`version_to_dict`] (`_version_to_dict`, plain `isoformat()`), both emitting an
//!   `IndexMap`-backed JSON object in the EXACT key order of the Python dicts.
//! * Owner gating: [`verify_doc_owner`] (`_verify_doc_owner`) and
//!   [`owner_session_filter`] (`_owner_session_filter`).
//! * File helpers: [`slug`] (`_slug`), [`upload_path_inside`] (`_upload_path_inside`),
//!   [`resolve_user_upload_path`] (`_resolve_user_upload_path`), [`locate_upload`]
//!   (`_locate_upload`), [`assert_pdf_marker_upload_owned`]
//!   (`_assert_pdf_marker_upload_owned`), [`derive_title`] (`_derive_title`), and the
//!   [`PDF_RENDER_SCALE`] const.
//!
//! ## Owner-scoped upload resolution
//! Upstream hardened the upload lookups against cross-owner reads: `_locate_upload`
//! no longer walks the filesystem itself but delegates to
//! `UploadHandler.resolve_upload(file_id, owner=…, auth_manager=…)`, which enforces
//! the owner / admin checks, and then re-asserts containment with
//! `_upload_path_inside` (realpath + commonpath). `_resolve_user_upload_path` is the
//! shared core both `_locate_upload` and `_assert_pdf_marker_upload_owned` call. The
//! Rust [`resolve_user_upload_path`] / [`locate_upload`] take a
//! [`crate::src::upload_handler::UploadHandler`] + an `owner` + an optional
//! [`crate::core::auth::AuthManager`] (the Python `request.app.state.auth_manager`).
//!
//! ## ORM rows → Rust structs
//! In Python these helpers receive SQLAlchemy `Document` / `DocumentVersion`
//! instances. The Rust route module reads rows via raw `rusqlite`, so the row
//! shapes are modeled as the plain structs [`Document`] and [`DocumentVersion`]
//! carrying exactly the columns the serializers and owner check read. `getattr(doc,
//! "archived", False)` / `getattr(doc, "source_email_*", None)` are real columns on
//! the `Document` model (see `core/database.py`), so they map to ordinary fields —
//! the `getattr` default only ever fires for a hypothetical legacy in-memory object,
//! never for a row loaded from the table.
//!
//! ## Owner gating against raw rusqlite
//! `_verify_doc_owner(db, doc, user)` issues a follow-up `db.query(Session)` only on
//! the legacy fallback path (doc.owner is NULL). [`verify_doc_owner`] takes a
//! `&rusqlite::Connection` so it can run that same `SELECT owner FROM sessions`
//! lookup. `_owner_session_filter(q, user)` returns a *filtered query*; the raw-SQL
//! analogue route handlers reuse is the WHERE condition + bound owner param, exposed
//! as [`owner_session_filter`] → [`OwnerFilter`].


use indexmap::IndexMap;
use once_cell::sync::Lazy;
use regex::Regex;
use rusqlite::{Connection, OptionalExtension};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::core::auth::AuthManager;
use crate::routes::HttpException;
use crate::src::upload_handler::UploadHandler;

// ---------------------------------------------------------------------------
// Row models (the SQLAlchemy ORM instances these helpers operate on)
// ---------------------------------------------------------------------------

/// The slice of the `documents` table row that [`doc_to_dict`] and
/// [`verify_doc_owner`] read — `core/database.py`'s `Document` model.
///
/// `created_at` / `updated_at` are the SQLAlchemy SQLite datetime strings
/// (`"%Y-%m-%d %H:%M:%S[.%f]"`), `None` when the column is NULL. `archived` is a
/// real `Boolean` column (`getattr(doc, "archived", False)` always hits it).
#[derive(Debug, Clone)]
pub struct Document {
    pub id: String,
    pub session_id: Option<String>,
    pub title: Option<String>,
    pub language: Option<String>,
    pub current_content: Option<String>,
    pub version_count: Option<i64>,
    pub is_active: Option<bool>,
    pub archived: Option<bool>,
    pub owner: Option<String>,
    pub created_at: Option<String>,
    pub updated_at: Option<String>,
    pub source_email_uid: Option<String>,
    pub source_email_folder: Option<String>,
    pub source_email_account_id: Option<String>,
    pub source_email_message_id: Option<String>,
}

/// The slice of the `document_versions` table row that [`version_to_dict`] reads —
/// `core/database.py`'s `DocumentVersion` model.
#[derive(Debug, Clone)]
pub struct DocumentVersion {
    pub id: String,
    pub document_id: String,
    pub version_number: i64,
    pub content: Option<String>,
    pub summary: Option<String>,
    pub source: Option<String>,
    pub created_at: Option<String>,
}

// ---------------------------------------------------------------------------
// Request schemas
// ---------------------------------------------------------------------------

/// `class DocumentCreate(BaseModel)` — JSON body for `POST /api/documents`.
///
/// `session_id`/`language` are `Optional[str] = None`; `title` defaults to
/// `"Untitled"`, `content` to `""`. `#[serde(default = …)]` reproduces each default
/// when the key is absent.
#[derive(Debug, Clone, Deserialize)]
pub struct DocumentCreate {
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default = "default_title")]
    pub title: String,
    #[serde(default)]
    pub language: Option<String>,
    #[serde(default)]
    pub content: String,
}

fn default_title() -> String {
    "Untitled".to_string()
}

/// `class DocumentUpdate(BaseModel)` — JSON body for `PUT /api/documents/{id}`.
///
/// `content` is required (`str`, no default → pydantic raises 422 if absent);
/// `summary` is `Optional[str] = None`.
#[derive(Debug, Clone, Deserialize)]
pub struct DocumentUpdate {
    pub content: String,
    #[serde(default)]
    pub summary: Option<String>,
}

/// `class DocumentPatch(BaseModel)` — JSON body for `PATCH /api/documents/{id}`.
///
/// All three fields are `Optional[str] = None`; `session_id` links/unlinks the doc
/// to a session.
#[derive(Debug, Clone, Deserialize)]
pub struct DocumentPatch {
    #[serde(default)]
    pub title: Option<String>,
    #[serde(default)]
    pub language: Option<String>,
    #[serde(default)]
    pub session_id: Option<String>,
}

// ---------------------------------------------------------------------------
// Serializers
// ---------------------------------------------------------------------------

/// `_doc_to_dict(doc)` — serialize a [`Document`] to its JSON object.
///
/// Key order is EXACTLY the Python dict literal; `IndexMap` preserves insertion
/// order. `created_at` / `updated_at` render with `isoformat() + "Z"` (note the
/// trailing `Z`, unlike [`version_to_dict`]) when present, else `null`. `archived`
/// is `bool(getattr(doc, "archived", False))` → coerce a NULL column to `false`.
pub fn doc_to_dict(doc: &Document) -> Value {
    let mut m: IndexMap<String, Value> = IndexMap::new();
    m.insert("id".into(), json!(doc.id));
    m.insert("session_id".into(), json!(doc.session_id));
    m.insert("title".into(), json!(doc.title));
    m.insert("language".into(), json!(doc.language));
    m.insert("current_content".into(), json!(doc.current_content));
    m.insert("version_count".into(), json!(doc.version_count));
    m.insert("is_active".into(), json!(doc.is_active));
    // bool(getattr(doc, "archived", False)) — NULL → False.
    m.insert("archived".into(), json!(doc.archived.unwrap_or(false)));
    // (doc.created_at.isoformat() + "Z") if doc.created_at else None
    m.insert("created_at".into(), iso_z_or_null(doc.created_at.as_deref()));
    m.insert("updated_at".into(), iso_z_or_null(doc.updated_at.as_deref()));
    m.insert("source_email_uid".into(), json!(doc.source_email_uid));
    m.insert("source_email_folder".into(), json!(doc.source_email_folder));
    m.insert(
        "source_email_account_id".into(),
        json!(doc.source_email_account_id),
    );
    m.insert(
        "source_email_message_id".into(),
        json!(doc.source_email_message_id),
    );
    Value::Object(m.into_iter().collect())
}

/// `_version_to_dict(v)` — serialize a [`DocumentVersion`] to its JSON object.
///
/// `created_at` uses PLAIN `isoformat()` (NO trailing `Z`, unlike [`doc_to_dict`]),
/// `null` when absent.
pub fn version_to_dict(v: &DocumentVersion) -> Value {
    let mut m: IndexMap<String, Value> = IndexMap::new();
    m.insert("id".into(), json!(v.id));
    m.insert("document_id".into(), json!(v.document_id));
    m.insert("version_number".into(), json!(v.version_number));
    m.insert("content".into(), json!(v.content));
    m.insert("summary".into(), json!(v.summary));
    m.insert("source".into(), json!(v.source));
    // v.created_at.isoformat() if v.created_at else None
    m.insert("created_at".into(), iso_or_null(v.created_at.as_deref()));
    Value::Object(m.into_iter().collect())
}

/// `(dt.isoformat() + "Z") if dt else None` — stored SQLite datetime → ISO + `Z`,
/// or `null` when absent/empty.
fn iso_z_or_null(stored: Option<&str>) -> Value {
    match stored.filter(|s| !s.is_empty()) {
        Some(s) => json!(format!("{}Z", crate::pydatetime::to_isoformat(s))),
        None => Value::Null,
    }
}

/// `dt.isoformat() if dt else None` — stored SQLite datetime → plain ISO, or `null`.
fn iso_or_null(stored: Option<&str>) -> Value {
    match stored.filter(|s| !s.is_empty()) {
        Some(s) => json!(crate::pydatetime::to_isoformat(s)),
        None => Value::Null,
    }
}

// ---------------------------------------------------------------------------
// Owner gating
// ---------------------------------------------------------------------------

/// `_verify_doc_owner(db, doc, user)` — verify `user` owns `doc`, else raise.
///
/// Mirrors the Python branch-for-branch:
/// * `user is None` → `HTTPException(403, "Authentication required")`.
/// * `doc.owner is not None` → trust the column: equal owner returns `Ok(())`,
///   otherwise `HTTPException(404, "Document not found")`.
/// * Legacy fallback (owner NULL): a missing `session_id` is a 404; otherwise look
///   up the linked session's owner (`db.query(Session)…`) and 404 unless it matches.
///
/// The session lookup is a raw `SELECT owner FROM sessions WHERE id = ?` — the
/// `rusqlite` analogue of `db.query(DbSession).filter(...).first()`. A DB error on
/// that lookup bubbles as a 500 (an unhandled exception inside the FastAPI handler).
pub fn verify_doc_owner(
    db: &Connection,
    doc: &Document,
    user: Option<&str>,
) -> Result<(), HttpException> {
    // if user is None: raise HTTPException(403, "Authentication required")
    let user = match user {
        None => return Err(HttpException::new(403, "Authentication required")),
        Some(u) => u,
    };
    // if doc.owner is not None:
    if let Some(owner) = doc.owner.as_deref() {
        if owner != user {
            return Err(HttpException::new(404, "Document not found"));
        }
        return Ok(());
    }
    // Legacy fallback: derive ownership from the linked session.
    // if not doc.session_id: raise HTTPException(404, "Document not found")
    let sid = match doc.session_id.as_deref() {
        Some(s) if !s.is_empty() => s,
        _ => return Err(HttpException::new(404, "Document not found")),
    };
    // session = db.query(DbSession).filter(DbSession.id == doc.session_id).first()
    let session_owner: Option<Option<String>> = db
        .query_row(
            "SELECT owner FROM sessions WHERE id = ?1",
            rusqlite::params![sid],
            |r| r.get::<_, Option<String>>(0),
        )
        .optional()
        .map_err(|e| {
            crate::pylog::error(&format!("document_helpers DB error: {e}"));
            HttpException::new(500, "Internal Server Error")
        })?;
    // if not session or session.owner != user: raise HTTPException(404, ...)
    match session_owner {
        Some(Some(o)) if o == user => Ok(()),
        _ => Err(HttpException::new(404, "Document not found")),
    }
}

/// `_owner_session_filter(q, user)` — the WHERE condition restricting a `documents`
/// query to `user`'s rows, returned as an [`OwnerFilter`] the route module folds
/// into its raw SQL.
///
/// Python returns a filtered SQLAlchemy query: `q.filter(False)` when `user is None`
/// (matches nothing), else `q.filter(Document.owner == user)`. The raw-SQL analogue
/// is a boolean WHERE fragment plus the bound owner value:
/// * `user is None` → [`OwnerFilter::None_`] → SQL `"0"` (a constant-false predicate,
///   exactly `filter(False)`), no param.
/// * otherwise → [`OwnerFilter::Owner`] → SQL `"owner = ?"` with `user` as the bound
///   param.
pub fn owner_session_filter(user: Option<&str>) -> OwnerFilter {
    match user {
        None => OwnerFilter::None_,
        Some(u) => OwnerFilter::Owner(u.to_string()),
    }
}

/// The outcome of [`owner_session_filter`]: either the constant-false filter
/// (`q.filter(False)`) or an `owner = ?` predicate with its bound value.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum OwnerFilter {
    /// `q.filter(False)` — matches no rows (`user is None`).
    None_,
    /// `q.filter(Document.owner == user)` — the owner to bind.
    Owner(String),
}

impl OwnerFilter {
    /// The SQL WHERE predicate fragment: `"0"` for the false filter, `"owner = ?"`
    /// for the owner match. Callers `AND` this into their query and, when
    /// [`Self::param`] is `Some`, append the value to their params.
    pub fn sql(&self) -> &'static str {
        match self {
            OwnerFilter::None_ => "0",
            OwnerFilter::Owner(_) => "owner = ?",
        }
    }

    /// The bound owner value (`Some` only for [`OwnerFilter::Owner`]).
    pub fn param(&self) -> Option<&str> {
        match self {
            OwnerFilter::None_ => None,
            OwnerFilter::Owner(o) => Some(o),
        }
    }
}

// ---------------------------------------------------------------------------
// File / title helpers
// ---------------------------------------------------------------------------

// re.sub(r'\.pdf$', '', s, flags=re.IGNORECASE) — drop a trailing ".pdf".
static SLUG_PDF_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)\.pdf$").unwrap());
// re.sub(r'\s+', '_', s) — runs of whitespace → single underscore.
static SLUG_WS_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\s+").unwrap());
// re.sub(r'[^A-Za-z0-9._-]', '', s) — drop everything outside the safe set.
static SLUG_UNSAFE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[^A-Za-z0-9._-]").unwrap());
// re.sub(r'_+', '_', s) — collapse underscore runs.
static SLUG_UNDERSCORE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"_+").unwrap());

/// `_slug(name)` — filesystem-friendly version of a document title.
///
/// Whitespace → `_`, other unsafe punctuation dropped; preserves letters, digits,
/// dot, hyphen, underscore. Idempotent. Empty result → `"form"`.
pub fn slug(name: &str) -> String {
    // s = (name or "").strip()
    let s = name.trim();
    // s = re.sub(r'\.pdf$', '', s, flags=re.IGNORECASE)
    let s = SLUG_PDF_RE.replace(s, "");
    // s = re.sub(r'\s+', '_', s)
    let s = SLUG_WS_RE.replace_all(&s, "_");
    // s = re.sub(r'[^A-Za-z0-9._-]', '', s)
    let s = SLUG_UNSAFE_RE.replace_all(&s, "");
    // s = re.sub(r'_+', '_', s).strip('_')
    let s = SLUG_UNDERSCORE_RE.replace_all(&s, "_");
    let s = s.trim_matches('_');
    // return s or "form"
    if s.is_empty() {
        "form".to_string()
    } else {
        s.to_string()
    }
}

/// DPI scale for the interactive PDF view. ~150 DPI (2x of 72 PDF user-units).
/// `_PDF_RENDER_SCALE = 2.0`.
pub const PDF_RENDER_SCALE: f64 = 2.0;

/// `os.path.abspath(path)` — lexical absolutize against the cwd (no filesystem /
/// symlink resolution), used only by the `_locate_upload` fallback that builds a
/// fresh `UploadHandler` when none is supplied. Mirrors CPython's `abspath`
/// (normpath of cwd-joined path) the same way `app_helpers::abspath` does; that
/// helper is private to its module, so the small lexical form is inlined here.
fn abspath(path: &str) -> String {
    use std::path::{Path, PathBuf};
    let p = Path::new(path);
    let joined: PathBuf = if p.is_absolute() {
        p.to_path_buf()
    } else {
        let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
        cwd.join(p)
    };
    // Lexical normalization (collapse `.` / `..` without touching the fs).
    let mut out: Vec<std::ffi::OsString> = Vec::new();
    let mut prefix = String::new();
    for comp in joined.components() {
        use std::path::Component;
        match comp {
            Component::Prefix(p) => prefix = p.as_os_str().to_string_lossy().into_owned(),
            Component::RootDir => out.clear(),
            Component::CurDir => {}
            Component::ParentDir => {
                out.pop();
            }
            Component::Normal(c) => out.push(c.to_os_string()),
        }
    }
    let mut s = prefix;
    s.push('/');
    s.push_str(
        &out.iter()
            .map(|c| c.to_string_lossy().into_owned())
            .collect::<Vec<_>>()
            .join("/"),
    );
    if s.len() > 1 && s.ends_with('/') {
        s.pop();
    }
    s
}

/// `_upload_path_inside(upload_dir, path)` — is `path` contained within `upload_dir`?
///
/// Python is `os.path.commonpath([realpath(upload_dir), realpath(path)]) ==
/// realpath(upload_dir)`, with any exception (mixed absolute/relative, different
/// drives on Windows) caught and returning `false`. That is exactly the realpath +
/// commonpath containment check `app_helpers::inside_base_dir` already performs
/// (the same helper `UploadHandler::inside_upload_dir` is built on), so we delegate.
pub fn upload_path_inside(upload_dir: &str, path: &str) -> bool {
    crate::src::app_helpers::inside_base_dir(upload_dir, path)
}

/// `_resolve_user_upload_path(upload_handler, upload_id, owner, auth_manager)` —
/// resolve an upload id to a filesystem path the caller may read.
///
/// Faithful to the Python:
/// * A `None` handler (`upload_handler is None`) yields `None`. The Rust caller
///   passes `Option<&UploadHandler>`, so that branch is the `None` arm here.
/// * `resolved = upload_handler.resolve_upload(upload_id, owner=owner,
///   auth_manager=auth_manager)` — Python omits `allow_admin`, which defaults to
///   `True`, so we pass `true`. `resolve_upload` already enforces the owner / admin
///   gate and returns `None` (the falsy "not a dict / empty" cases) when the caller
///   may not read it.
/// * Re-assert containment: `if path and upload_dir and not
///   _upload_path_inside(upload_dir, path): warn; return None`. `resolve_upload`
///   already checks this internally, but the Python belt-and-suspenders re-check is
///   ported verbatim.
pub fn resolve_user_upload_path(
    upload_handler: Option<&UploadHandler>,
    upload_id: &str,
    owner: Option<&str>,
    auth_manager: Option<&AuthManager>,
) -> Option<String> {
    // if upload_handler is None: return None
    let upload_handler = upload_handler?;
    // resolved = upload_handler.resolve_upload(upload_id, owner=…, auth_manager=…)
    // (allow_admin defaults to True in Python).
    let resolved = upload_handler.resolve_upload(upload_id, owner, auth_manager, true);
    // if not isinstance(resolved, dict) or not resolved: return None
    // resolve_upload returns Option<Map>; None / empty map are both falsy.
    let resolved = resolved.filter(|m| !m.is_empty())?;
    // path = resolved.get("path")
    let path = resolved.get("path").and_then(Value::as_str)?.to_string();
    // upload_dir = getattr(upload_handler, "upload_dir", None)
    let upload_dir = upload_handler.upload_dir();
    // if path and upload_dir and not _upload_path_inside(upload_dir, path): None
    if !path.is_empty() && !upload_dir.is_empty() && !upload_path_inside(upload_dir, &path) {
        crate::pylog::warning(&format!("Upload path outside upload directory: {path}"));
        return None;
    }
    Some(path)
}

/// `_locate_upload(upload_dir, file_id, owner=None, auth_manager=None,
/// upload_handler=None)` — find an upload by its filename ID via
/// `UploadHandler.resolve_upload`.
///
/// Upstream replaced the old direct/index/`os.walk` lookup with an owner-scoped
/// delegation: the resolve enforces owner / admin containment so a cross-user PDF id
/// can no longer be read. Faithful to the Python:
/// * When no handler is supplied (`upload_handler is None`), construct one rooted at
///   `os.path.dirname(os.path.abspath(upload_dir))` with `upload_dir` as the upload
///   dir, exactly like the Python fallback. (The route module always threads its
///   `AppState.upload_handler`, so the `Some` arm is the live path.)
/// * Then `_resolve_user_upload_path(upload_handler, file_id, owner, auth_manager)`.
pub fn locate_upload(
    upload_dir: &str,
    file_id: &str,
    owner: Option<&str>,
    auth_manager: Option<&AuthManager>,
    upload_handler: Option<&UploadHandler>,
) -> Option<String> {
    // if upload_handler is None:
    //     base_dir = os.path.dirname(os.path.abspath(upload_dir))
    //     upload_handler = UploadHandler(base_dir, upload_dir)
    match upload_handler {
        Some(uh) => resolve_user_upload_path(Some(uh), file_id, owner, auth_manager),
        None => {
            let base_dir = crate::pyos::path::dirname(&abspath(upload_dir));
            let uh = UploadHandler::new(&base_dir, upload_dir);
            resolve_user_upload_path(Some(&uh), file_id, owner, auth_manager)
        }
    }
}

/// `_assert_pdf_marker_upload_owned(request, content, user, upload_handler)` —
/// reject document content whose `pdf_source` marker points at another user's
/// upload.
///
/// Called by `document_routes` on create / update. Faithful to the Python:
/// * `if upload_handler is None: return` — nothing to check.
/// * `upload_id = find_source_upload_id(content or "")`; a missing / invalid marker
///   (`not upload_id`) returns without error.
/// * Otherwise resolve the marker's upload id for `user` (with `auth_manager`,
///   which Python reads off `request.app.state.auth_manager`; the Rust caller threads
///   `AppState.auth`). If it does not resolve to a readable path, raise
///   `HTTPException(400, "Document PDF marker references an upload you do not own")`.
///
/// Python takes the FastAPI `Request` only to reach `request.app.state.auth_manager`;
/// the Rust signature takes the `auth_manager` directly (the route handler already
/// holds `AppState.auth`).
pub fn assert_pdf_marker_upload_owned(
    content: &str,
    user: Option<&str>,
    upload_handler: Option<&UploadHandler>,
    auth_manager: Option<&AuthManager>,
) -> Result<(), HttpException> {
    // if upload_handler is None: return
    let upload_handler = match upload_handler {
        Some(uh) => uh,
        None => return Ok(()),
    };
    // upload_id = find_source_upload_id(content or "")
    let upload_id = match crate::src::pdf_form_doc::find_source_upload_id(content) {
        Some(id) => id,
        // if not upload_id: return
        None => return Ok(()),
    };
    // auth_manager = getattr(getattr(request.app, "state", None), "auth_manager", None)
    // (threaded in by the caller).
    // if not _resolve_user_upload_path(upload_handler, upload_id, user, auth_manager):
    if resolve_user_upload_path(Some(upload_handler), &upload_id, user, auth_manager).is_none() {
        return Err(HttpException::new(
            400,
            "Document PDF marker references an upload you do not own",
        ));
    }
    Ok(())
}

// ^#{1,3}\s+(.+) with re.MULTILINE, applied via re.match (anchored at pos 0).
// `.` excludes newlines (no DOTALL), so the capture stops at the first newline.
static MD_HEADER_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?m)^#{1,3}\s+(.+)").unwrap());
// <h[1-3][^>]*>([^<]+)</h[1-3]> with re.IGNORECASE, via re.search.
static HTML_HEADING_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)<h[1-3][^>]*>([^<]+)</h[1-3]>").unwrap());
// re.sub(r'[:#*`]+$', '', line) — trim trailing markdown punctuation off a line.
static TRAILING_PUNCT_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[:#*`]+$").unwrap());

/// `_derive_title(content)` — derive a title from document content.
///
/// Branch order, faithful to the Python:
/// 1. Empty (after strip) → `"Untitled"`.
/// 2. A leading markdown header (`# … `/`## …`/`### …`, anchored at the start —
///    `re.match` only tries position 0 even under `MULTILINE`) → its text.
/// 3. The first HTML `<h1>`-`<h3>` heading anywhere (`re.search`, case-insensitive).
/// 4. The first non-empty line whose length is `2..=60`, with trailing `:#*`` `
///    stripped → that (or `"Untitled"` if it strips to empty).
/// 5. Otherwise `"Untitled"`.
///
/// Each candidate title longer than 50 chars is truncated to its first 48 chars +
/// `"…"`. Length and slicing are by Unicode codepoint, matching Python `len` / `[:48]`.
pub fn derive_title(content: &str) -> String {
    // text = content.strip()
    let text = content.trim();
    // if not text: return "Untitled"
    if text.is_empty() {
        return "Untitled".to_string();
    }

    // Markdown header — re.match anchors at pos 0; `^#{1,3}\s+(.+)` therefore only
    // matches when the string starts with the header.
    if let Some(c) = MD_HEADER_RE.captures(text) {
        // re.match requires the match to begin at position 0.
        if c.get(0).map(|m| m.start()) == Some(0) {
            let title = c.get(1).unwrap().as_str().trim();
            return truncate50(title);
        }
    }

    // HTML heading — re.search, first match anywhere.
    if let Some(c) = HTML_HEADING_RE.captures(text) {
        let title = c.get(1).unwrap().as_str().trim();
        return truncate50(title);
    }

    // First non-empty line (if short enough).
    for line in text.split('\n') {
        let line = line.trim();
        // if line and 2 <= len(line) <= 60:
        let n = line.chars().count();
        if !line.is_empty() && (2..=60).contains(&n) {
            // title = re.sub(r'[:#*`]+$', '', line).strip()
            let title = TRAILING_PUNCT_RE.replace(line, "");
            let title = title.trim();
            // if title and len(title) > 50: title = title[:48] + "…"
            // return title or "Untitled"
            if title.is_empty() {
                return "Untitled".to_string();
            }
            return truncate50(title);
        }
    }

    "Untitled".to_string()
}

/// `if len(title) > 50: title = title[:48] + "…"` — codepoint-based truncation.
fn truncate50(title: &str) -> String {
    if title.chars().count() > 50 {
        let head: String = title.chars().take(48).collect();
        format!("{head}…")
    } else {
        title.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn doc() -> Document {
        Document {
            id: "d1".into(),
            session_id: Some("s1".into()),
            title: Some("Hello".into()),
            language: Some("markdown".into()),
            current_content: Some("body".into()),
            version_count: Some(3),
            is_active: Some(true),
            archived: None,
            owner: Some("alice".into()),
            created_at: Some("2024-01-02 03:04:05.000000".into()),
            updated_at: Some("2024-01-02 03:04:05.500000".into()),
            source_email_uid: None,
            source_email_folder: None,
            source_email_account_id: None,
            source_email_message_id: Some("<mid>".into()),
        }
    }

    #[test]
    fn doc_to_dict_key_order_and_iso_z() {
        let v = doc_to_dict(&doc());
        let obj = v.as_object().unwrap();
        let keys: Vec<&str> = obj.keys().map(String::as_str).collect();
        assert_eq!(
            keys,
            vec![
                "id",
                "session_id",
                "title",
                "language",
                "current_content",
                "version_count",
                "is_active",
                "archived",
                "created_at",
                "updated_at",
                "source_email_uid",
                "source_email_folder",
                "source_email_account_id",
                "source_email_message_id",
            ]
        );
        // bool(getattr(doc, "archived", False)) — NULL → false.
        assert_eq!(obj["archived"], json!(false));
        // created_at: no fraction → no microseconds, then "+Z".
        assert_eq!(obj["created_at"], json!("2024-01-02T03:04:05Z"));
        // updated_at: non-zero fraction → 6 digits, then "+Z".
        assert_eq!(obj["updated_at"], json!("2024-01-02T03:04:05.500000Z"));
    }

    #[test]
    fn version_to_dict_plain_iso_no_z() {
        let v = DocumentVersion {
            id: "v1".into(),
            document_id: "d1".into(),
            version_number: 2,
            content: Some("x".into()),
            summary: None,
            source: Some("ai".into()),
            created_at: Some("2024-01-02 03:04:05.000000".into()),
        };
        let out = version_to_dict(&v);
        let obj = out.as_object().unwrap();
        let keys: Vec<&str> = obj.keys().map(String::as_str).collect();
        assert_eq!(
            keys,
            vec![
                "id",
                "document_id",
                "version_number",
                "content",
                "summary",
                "source",
                "created_at",
            ]
        );
        // Plain isoformat — NO trailing Z.
        assert_eq!(obj["created_at"], json!("2024-01-02T03:04:05"));
    }

    #[test]
    fn verify_owner_branches() {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(
            "CREATE TABLE sessions (id TEXT PRIMARY KEY, owner TEXT);
             INSERT INTO sessions (id, owner) VALUES ('s1', 'bob');",
        )
        .unwrap();

        // user is None → 403.
        let d = doc();
        let e = verify_doc_owner(&conn, &d, None).unwrap_err();
        assert_eq!(e.status_code, 403);

        // owner column matches → Ok.
        assert!(verify_doc_owner(&conn, &d, Some("alice")).is_ok());

        // owner column mismatch → 404.
        let e = verify_doc_owner(&conn, &d, Some("mallory")).unwrap_err();
        assert_eq!(e.status_code, 404);

        // Legacy fallback: owner NULL, session_id links to session owned by bob.
        let mut legacy = doc();
        legacy.owner = None;
        assert!(verify_doc_owner(&conn, &legacy, Some("bob")).is_ok());
        let e = verify_doc_owner(&conn, &legacy, Some("alice")).unwrap_err();
        assert_eq!(e.status_code, 404);

        // Legacy fallback: owner NULL and no session_id → 404.
        let mut orphan = doc();
        orphan.owner = None;
        orphan.session_id = None;
        let e = verify_doc_owner(&conn, &orphan, Some("bob")).unwrap_err();
        assert_eq!(e.status_code, 404);
    }

    #[test]
    fn owner_filter_sql_and_param() {
        let f = owner_session_filter(None);
        assert_eq!(f, OwnerFilter::None_);
        assert_eq!(f.sql(), "0");
        assert_eq!(f.param(), None);

        let f = owner_session_filter(Some("alice"));
        assert_eq!(f.sql(), "owner = ?");
        assert_eq!(f.param(), Some("alice"));
    }

    #[test]
    fn slug_cases() {
        assert_eq!(slug("  My Form.PDF "), "My_Form");
        assert_eq!(slug("a/b:c*d"), "abcd");
        assert_eq!(slug("__weird__name__"), "weird_name");
        assert_eq!(slug("!!!"), "form");
        assert_eq!(slug(""), "form");
        // Idempotent.
        assert_eq!(slug(&slug("My Form")), slug("My Form"));
    }

    #[test]
    fn derive_title_cases() {
        assert_eq!(derive_title(""), "Untitled");
        assert_eq!(derive_title("   \n  "), "Untitled");
        assert_eq!(derive_title("## Heading Here\nbody"), "Heading Here");
        // re.match anchored: a header not at the start falls through.
        assert_eq!(
            derive_title("intro line goes here\n# Later Header"),
            "intro line goes here"
        );
        assert_eq!(
            derive_title("<p>x</p><h2 class='a'>HTML Title</h2>"),
            "HTML Title"
        );
        // First non-empty line, trailing punctuation stripped.
        assert_eq!(derive_title("Section:###"), "Section");
        // Too-long line is truncated to 48 chars + ellipsis.
        let long = "x".repeat(60);
        let got = derive_title(&long);
        assert_eq!(got.chars().count(), 49);
        assert!(got.ends_with('…'));
        // A line longer than 60 chars is skipped (not 2..=60) → Untitled.
        let huge: String = "y".repeat(80);
        assert_eq!(derive_title(&huge), "Untitled");
    }

    /// A syntactically valid upload id (32 hex chars + extension) so the
    /// `validate_upload_id` gate inside `resolve_upload` / `find_source_upload_id`
    /// passes.
    const UID: &str = "0123456789abcdef0123456789abcdef.pdf";

    /// Build an `UploadHandler` rooted at a fresh temp upload dir with one upload
    /// (`UID`) physically present and indexed in `uploads.json` with the given
    /// `owner` (a JSON string, or null when `owner` is `None`). Returns the handler,
    /// the upload dir path, and the on-disk file path.
    fn handler_with_upload(tag: &str, owner: Option<&str>) -> (UploadHandler, String, String) {
        let tmp = std::env::temp_dir().join(format!(
            "dh_{tag}_{}_{:?}",
            std::process::id(),
            std::thread::current().id()
        ));
        let _ = std::fs::remove_dir_all(&tmp);
        std::fs::create_dir_all(&tmp).unwrap();
        let upload_dir = tmp.to_string_lossy().into_owned();
        // The file lives directly under the upload dir so inside_upload_dir passes.
        let file_path = tmp.join(UID);
        std::fs::write(&file_path, b"%PDF-1.4 body").unwrap();
        let file_path_s = file_path.to_string_lossy().into_owned();

        let owner_val = owner.map(|o| json!(o)).unwrap_or(Value::Null);
        let idx = json!({
            "deadbeef": {
                "id": UID,
                "path": file_path_s,
                "owner": owner_val,
                "mime": "application/pdf",
                "name": UID,
            }
        });
        std::fs::write(tmp.join("uploads.json"), idx.to_string()).unwrap();

        // base_dir is the parent of upload_dir (UploadHandler::new makedirs it).
        let base_dir = crate::pyos::path::dirname(&upload_dir);
        let uh = UploadHandler::new(&base_dir, &upload_dir);
        (uh, upload_dir, file_path_s)
    }

    #[test]
    fn upload_path_inside_containment() {
        let tmp = std::env::temp_dir().join(format!("dh_inside_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);
        std::fs::create_dir_all(tmp.join("sub")).unwrap();
        let dir = tmp.to_string_lossy().into_owned();
        let inside = tmp.join("sub").join("f.bin");
        std::fs::write(&inside, b"x").unwrap();

        assert!(upload_path_inside(&dir, &inside.to_string_lossy()));
        // A sibling outside the upload dir is not contained.
        assert!(!upload_path_inside(&dir, "/etc/passwd"));
        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn locate_upload_owner_scoped() {
        // Auth disabled (unconfigured) + the upload owned by alice. With no owner
        // and an owner on the upload, resolve_upload denies → None.
        let (uh, dir, _) = handler_with_upload("loc_owned", Some("alice"));
        let no_auth = AuthManager::new();
        assert_eq!(
            locate_upload(&dir, UID, None, Some(&no_auth), Some(&uh)),
            None
        );
        // The correct owner reads it (auth not configured, owner matches).
        let got = locate_upload(&dir, UID, Some("alice"), Some(&no_auth), Some(&uh));
        assert!(got.is_some());
        assert!(got.unwrap().ends_with(UID));
        // A different owner is denied → None (the cross-user read the fix closes).
        assert_eq!(
            locate_upload(&dir, UID, Some("mallory"), Some(&no_auth), Some(&uh)),
            None
        );
        let _ = std::fs::remove_dir_all(crate::pyos::path::dirname(&dir));
    }

    #[test]
    fn locate_upload_ownerless_anonymous() {
        // An upload with no owner is readable anonymously when auth is disabled.
        // Python: auth_configured = bool(auth_manager and ...) — passing auth_manager=None
        // gives auth_configured=False, the "auth disabled" path that lets an ownerless
        // upload resolve for an anonymous (owner=None) caller.  AuthManager::new() loads
        // the live data/auth.json (which has real users), so is_configured()=true, which
        // would incorrectly trigger the early "auth_configured && not owner" denial.
        // Pass None for auth_manager to faithfully represent an unconfigured / disabled
        // auth setup, matching the Python test scenario.
        let (uh, dir, _) = handler_with_upload("loc_anon", None);
        let got = locate_upload(&dir, UID, None, None, Some(&uh));
        assert!(got.is_some());
        assert!(got.unwrap().ends_with(UID));
        let _ = std::fs::remove_dir_all(crate::pyos::path::dirname(&dir));
    }

    #[test]
    fn locate_upload_missing_id() {
        // An unknown (but well-formed) id resolves to nothing.
        let (uh, dir, _) = handler_with_upload("loc_missing", None);
        let other = "ffffffffffffffffffffffffffffffff.pdf";
        let no_auth = AuthManager::new();
        assert_eq!(
            locate_upload(&dir, other, None, Some(&no_auth), Some(&uh)),
            None
        );
        // A malformed id is rejected by validate_upload_id inside resolve_upload.
        assert_eq!(
            locate_upload(&dir, "not-a-valid-id", None, Some(&no_auth), Some(&uh)),
            None
        );
        let _ = std::fs::remove_dir_all(crate::pyos::path::dirname(&dir));
    }

    #[test]
    fn resolve_user_upload_path_none_handler() {
        // upload_handler is None → None (no resolution attempted).
        assert_eq!(resolve_user_upload_path(None, UID, None, None), None);
    }

    #[test]
    fn assert_pdf_marker_owned_branches() {
        let (uh, dir, _) = handler_with_upload("marker", Some("alice"));
        let no_auth = AuthManager::new();

        // No upload_handler → Ok (nothing to check).
        let marker = format!("<!-- pdf_source upload_id=\"{UID}\" -->\nbody");
        assert!(assert_pdf_marker_upload_owned(&marker, Some("alice"), None, None).is_ok());

        // No marker in the content → Ok.
        assert!(
            assert_pdf_marker_upload_owned("plain content", Some("alice"), Some(&uh), Some(&no_auth))
                .is_ok()
        );

        // Owner matches the upload → Ok.
        assert!(assert_pdf_marker_upload_owned(
            &marker,
            Some("alice"),
            Some(&uh),
            Some(&no_auth)
        )
        .is_ok());

        // Marker points at another user's upload → 400.
        let e = assert_pdf_marker_upload_owned(&marker, Some("mallory"), Some(&uh), Some(&no_auth))
            .unwrap_err();
        assert_eq!(e.status_code, 400);

        let _ = std::fs::remove_dir_all(crate::pyos::path::dirname(&dir));
    }
}
