// routes/personal_routes.rs  <- routes/personal_routes.py
//! Routes for personal documents management (wave-2 unit `personal_routes`).
//!
//! Faithful translation of `routes/personal_routes.py`'s `setup_personal_routes`
//! factory. Seven handlers under `APIRouter(prefix="/api/personal")`:
//!
//!   * `GET  /api/personal`               — list indexed files + directories
//!   * `POST /api/personal/reload`        — re-walk the index
//!   * `POST /api/personal/add_directory` — add a directory to the RAG index
//!   * `DELETE /api/personal/remove_directory` — drop a directory from tracking/RAG
//!   * `POST /api/personal/upload`        — upload files straight into RAG
//!   * `DELETE /api/personal/file`        — delete a file from RAG + disk
//!
//! ## Shape (the integration substrate)
//! * `setup_personal_routes() -> Router<AppState>` mirrors the Python factory.
//!   The Python factory args (`personal_docs_manager`, `rag_manager`,
//!   `rag_available`) are reached through `State<AppState>` and the `&'static`
//!   [`crate::src::rag_singleton`] singleton instead of being threaded in, so the
//!   factory takes no params (matching every other ported `setup_x_routes`).
//! * `personal_docs_manager` is the shared `Arc<PersonalDocsManager>`
//!   (`AppState.personal_docs_manager`). The Python handlers mutate the one shared
//!   manager instance in place (`refresh_index`, `remove_directory`,
//!   `exclude_file`); the Rust manager owns interior mutability for exactly that
//!   reason, so every method is `&self` and callable through the `Arc`.
//!
//! ## The RAG-unavailable branch is the LIVE path (faithful, NOT a stub)
//! The Python `_rag()` helper calls `src.rag_singleton.get_rag_manager()`, which is
//! **disabled** and unconditionally returns `None` (VectorRAG/ChromaDB is unused;
//! see `routes/personal_routes.py` L34 + `src/rag_singleton.py`). So on the live
//! path `rag` is **always** `None`, and every `if rag:` guard takes the
//! rag-unavailable arm:
//!   * `add_directory` -> `raise HTTPException(503, "RAG system is not available")`;
//!   * `upload`        -> `raise HTTPException(503, "RAG system is not available …")`;
//!   * `remove_directory` -> skips the RAG removal (manager tracking still updated);
//!   * `delete_file`   -> `removed_chunks = 0` (no RAG delete), disk delete + exclude.
//! The Rust `rag()` mirrors that by calling the real ported
//! [`crate::src::rag_singleton::get_rag_manager`] (returns `None`) — the same
//! `None` Python produces, not a fabricated constant. This is the faithful
//! Python rag-unavailable behaviour, not a stub: the rag-available code (chunking
//! + `add_document` + `index_personal_documents` + `delete_by_source`) is
//! genuinely unreachable in both the Python and the Rust live app.
//!
//! ## Auth
//! Five of the six handlers carry `Depends(require_user)` + `Depends(require_admin)`
//! (in that signature order — FastAPI resolves sub-dependencies left-to-right, so
//! `require_user` runs first, then `require_admin`). `upload_files_to_rag` has NO
//! auth dependency (it only reads `get_current_user(request)` to stamp the
//! per-chunk `owner` metadata). The two gates are bridged via the F3
//! [`crate::routes::auth_adapter`] (`require_user` / `require_admin`).
//!
//! ## Path confinement (`_resolve_allowed_personal_dir`)
//! The Python function was updated from `os.path.abspath` (lexical only) to
//! `os.path.realpath` (resolves symlinks via `realpath(3)`) so that a symlink inside
//! `PERSONAL_DIR` pointing outside is caught before the `commonpath` check. The Rust
//! port mirrors this with `realpath_or_abspath`: try `std::fs::canonicalize` (the
//! `realpath(3)` wrapper); fall back to the lexical `abspath` when the path doesn't
//! exist yet. The `delete_file` uploads-dir check is a separate, unrelated guard that
//! still uses the lexical `abspath`/`commonpath` (Python also uses `abspath` there,
//! not `realpath`).
//!
//! ## No path collision
//! Every route is under `/api/personal*`, a prefix the inline `web/mod.rs` subset
//! never touches, so the aggregator merges this router without an axum
//! duplicate-`method`+`path` panic.


use std::collections::HashMap;
use std::path::{Component, Path, PathBuf};

use axum::extract::{ConnectInfo, Multipart, Query, State};
use axum::http::HeaderMap;
use axum::response::{IntoResponse, Response};
use axum::routing::{delete, get, post};
use axum::{Extension, Json, Router};
use serde::Deserialize;
use serde_json::json;
use std::net::SocketAddr;

use crate::core::constants::{BASE_DIR, PERSONAL_DIR};
use crate::core::middleware::INTERNAL_TOOL_HEADER;
use crate::pylog as logger;
use crate::routes::auth_adapter;
use crate::routes::{AppState, CurrentUser, HttpException};

/// `UPLOADS_DIR = os.path.join(BASE_DIR, "data", "personal_uploads")`.
///
/// NOTE: the Python hardcodes `BASE_DIR/data/...`, NOT the `DATA_DIR` constant —
/// so this deliberately joins onto `BASE_DIR` (the Python has no `ODYSSEUS_DATA_DIR`
/// override; matching `BASE_DIR/data` keeps byte-for-byte path parity with the live
/// app).
fn uploads_dir() -> String {
    let data = crate::pyos::path::join(&BASE_DIR, "data");
    crate::pyos::path::join(&data, "personal_uploads")
}

/// `class DirectoryRequest(BaseModel)` — the JSON body for `POST /add_directory`.
///
/// `directory: str = Field(..., min_length=1, max_length=500)`. The pydantic
/// constraints are enforced manually (FastAPI returns 422 on a violation, before
/// the handler body runs): missing/empty -> 422, > 500 chars -> 422.
#[derive(Debug, Deserialize)]
struct DirectoryRequest {
    directory: String,
}

/// `setup_personal_routes(personal_docs_manager, rag_manager, rag_available)` —
/// assemble the personal-docs router.
///
/// The Python registers, under `prefix="/api/personal"`, in this order:
/// `GET ""`, `POST /reload`, `POST /add_directory`, `DELETE /remove_directory`,
/// `POST /upload`, `DELETE /file`. Those resolve to the absolute paths below. The
/// paths are pairwise disjoint, so registration order is immaterial to matching;
/// we keep the Python source order for fidelity.
pub fn setup_personal_routes() -> Router<AppState> {
    Router::new()
        // @router.get("")  -> prefix + ""  == "/api/personal"
        .route("/api/personal", get(api_personal_list))
        // @router.post("/reload")
        .route("/api/personal/reload", post(api_personal_reload))
        // @router.post("/add_directory")
        .route("/api/personal/add_directory", post(add_directory_to_rag))
        // @router.delete("/remove_directory")
        .route(
            "/api/personal/remove_directory",
            delete(remove_directory_from_rag),
        )
        // @router.post("/upload")
        .route("/api/personal/upload", post(upload_files_to_rag))
        // @router.delete("/file")
        .route("/api/personal/file", delete(delete_file_from_rag))
}

// ===========================================================================
// Auth glue
// ===========================================================================

/// `Depends(require_user)` then `Depends(require_admin)` — the two-dependency gate
/// five handlers share, resolved in Python signature order (user first, admin
/// second). Returns the resolved owner (`""` in unconfigured single-user mode).
///
/// `require_user` needs the peer host for its unconfigured-loopback fallback
/// (`request.client.host`), pulled from `ConnectInfo<SocketAddr>` (absent in unit
/// tests / when not served via `into_make_service_with_connect_info`, so `Option`).
/// `require_admin` reads the `X-Odysseus-Internal-Token` header + the stamped user.
fn user_then_admin(
    state: &AppState,
    headers: &HeaderMap,
    user: Option<&str>,
    client_host: Option<&str>,
) -> Result<String, HttpException> {
    // owner: str = Depends(require_user)
    let owner = auth_adapter::require_user(user, state, client_host)?;
    // _admin: None = Depends(require_admin)
    let internal_header = headers.get(INTERNAL_TOOL_HEADER).and_then(|v| v.to_str().ok());
    auth_adapter::require_admin(state, internal_header, user)?;
    Ok(owner)
}

/// `request.state.current_user` / `request.client.host` resolved from the axum
/// extractors the auth gate / connect-info layer deliver.
fn user_str(user: &Option<Extension<CurrentUser>>) -> Option<&str> {
    user.as_ref().map(|Extension(u)| u.0.as_str())
}

/// `request.client.host` — the peer IP, or `None` when unknown.
fn host_str(ci: &Option<ConnectInfo<SocketAddr>>) -> Option<String> {
    ci.as_ref().map(|c| c.0.ip().to_string())
}

// ===========================================================================
// RAG availability — `_rag()`
// ===========================================================================

/// `_rag()` — "Get the current RAG manager, retrying init if needed."
///
/// Mirrors `routes/personal_routes.py`'s `_rag()`, which delegates to
/// `src.rag_singleton.get_rag_manager()`. That singleton is **disabled** and always
/// returns `None` (see the module header), so `rag()` is always `None` too — the
/// faithful live behaviour. Calling the real ported singleton (rather than a bare
/// `None`) keeps the binding honest: it is the same `None` Python computes.
fn rag() -> Option<()> {
    // `crate::src::rag_singleton::get_rag_manager()` returns `Option<Arc<VectorRAG>>`,
    // which is always `None`. We only need the presence/absence, so map to `Option<()>`.
    crate::src::rag_singleton::get_rag_manager().map(|_| ())
}

// ===========================================================================
// Handlers
// ===========================================================================

/// `GET /api/personal` — "Enhanced version that includes directories".
///
/// ```python
/// @router.get("")
/// def api_personal_list(owner = Depends(require_user), _admin = Depends(require_admin)):
///     files = [{"name": f["name"], "size": f["size"], "path": f.get("path", "")}
///              for f in personal_docs_manager.index]
///     directories = personal_docs_manager.get_indexed_directories() if hasattr(...) else []
///     return {"files": files, "directories": directories}
/// ```
async fn api_personal_list(
    State(s): State<AppState>,
    headers: HeaderMap,
    ci: Option<ConnectInfo<SocketAddr>>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    user_then_admin(&s, &headers, user_str(&user), host_str(&ci).as_deref())?;

    // files = [{"name", "size", "path"} for f in personal_docs_manager.index]
    let files = s.personal_docs_manager.list_index_entries();
    // directories = personal_docs_manager.get_indexed_directories()  (hasattr is True)
    let directories = s.personal_docs_manager.get_indexed_directories();
    Ok(Json(json!({ "files": files, "directories": directories })).into_response())
}

/// `POST /api/personal/reload`.
///
/// ```python
/// @router.post("/reload")
/// def api_personal_reload(owner = Depends(require_user), _admin = Depends(require_admin)):
///     personal_docs_manager.refresh_index()
///     return {"ok": True, "count": len(personal_docs_manager.index)}
/// ```
async fn api_personal_reload(
    State(s): State<AppState>,
    headers: HeaderMap,
    ci: Option<ConnectInfo<SocketAddr>>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    user_then_admin(&s, &headers, user_str(&user), host_str(&ci).as_deref())?;

    s.personal_docs_manager.refresh_index();
    let count = s.personal_docs_manager.index_len();
    Ok(Json(json!({ "ok": true, "count": count })).into_response())
}

/// `POST /api/personal/add_directory` — add a directory and all its
/// subdirectories/files to the RAG index.
///
/// On the live path `rag` is `None`, so this always reaches the
/// `raise HTTPException(503, "RAG system is not available")` arm (after the path
/// confinement + existence/dir checks). The rag-available indexing branch
/// (`rag.index_personal_documents` + `personal_docs_manager.add_directory`) is the
/// faithful unreachable arm.
async fn add_directory_to_rag(
    State(s): State<AppState>,
    headers: HeaderMap,
    ci: Option<ConnectInfo<SocketAddr>>,
    user: Option<Extension<CurrentUser>>,
    Json(directory_request): Json<DirectoryRequest>,
) -> Result<Response, HttpException> {
    // owner: str = Depends(require_user), _admin = Depends(require_admin)
    let _owner = user_then_admin(&s, &headers, user_str(&user), host_str(&ci).as_deref())?;

    // pydantic: directory min_length=1, max_length=500 (422 before the body runs).
    let directory = directory_request.directory;
    if directory.is_empty() || directory.chars().count() > 500 {
        return Err(HttpException::new(
            422,
            "directory must be between 1 and 500 characters",
        ));
    }

    // try: ... except HTTPException: raise; except Exception: 500
    // `_resolve_allowed_personal_dir` raises 400/403 (re-raised by the bare
    // `except HTTPException`).
    let directory = resolve_allowed_personal_dir(&directory)?;

    // Security check — ensure directory exists and is accessible.
    // if not os.path.exists(directory): raise HTTPException(404, ...)
    if !Path::new(&directory).exists() {
        return Err(HttpException::new(
            404,
            format!("Directory not found: {directory}"),
        ));
    }
    // if not os.path.isdir(directory): raise HTTPException(400, ...)
    if !Path::new(&directory).is_dir() {
        return Err(HttpException::new(
            400,
            format!("Path is not a directory: {directory}"),
        ));
    }

    logger::info(&format!("Adding directory to RAG: {directory}"));

    // rag = _rag(); if rag: ...  else: raise HTTPException(503, ...)
    // `rag` is always None on the live path (disabled singleton), so this is the
    // 503 arm — the faithful Python rag-unavailable branch.
    match rag() {
        Some(()) => {
            // UNREACHABLE on the live path (the singleton is disabled). The Python
            // rag-available arm calls `rag.index_personal_documents(directory,
            // owner=owner)` and on success `personal_docs_manager.add_directory(...)`.
            // The disabled `rag_singleton` never yields a manager, so this branch is
            // not portable to a live call; reaching it would be a contract change.
            unreachable!("rag_singleton::get_rag_manager() is disabled and returns None")
        }
        None => {
            // else: raise HTTPException(503, "RAG system is not available")
            Err(HttpException::new(503, "RAG system is not available"))
        }
    }
}

/// `DELETE /api/personal/remove_directory` — remove a directory from the RAG index.
///
/// `directory: str = Query(...)` — required query param (422 when absent). Always
/// removes the directory from `personal_docs_manager` tracking; the RAG vector-store
/// removal is best-effort and skipped on the live path (`rag` is `None`).
async fn remove_directory_from_rag(
    State(s): State<AppState>,
    headers: HeaderMap,
    ci: Option<ConnectInfo<SocketAddr>>,
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let _owner = user_then_admin(&s, &headers, user_str(&user), host_str(&ci).as_deref())?;

    // directory: str = Query(...)  — required; FastAPI 422 when the param is absent.
    let directory = match q.get("directory") {
        Some(d) => d.clone(),
        None => return Err(HttpException::new(422, "Field required: directory")),
    };

    // try: ... except HTTPException: raise; except Exception: 500
    // if not directory: raise HTTPException(400, "Directory path is required")
    if directory.is_empty() {
        return Err(HttpException::new(400, "Directory path is required"));
    }

    logger::info(&format!("Removing directory from RAG: {directory}"));

    // Always remove from personal_docs_manager tracking (hasattr 'remove_directory'
    // is True for the ported manager).
    s.personal_docs_manager.remove_directory(&directory);

    // Remove from RAG vector store (best-effort). `rag` is None on the live path,
    // so the `if rag:` block is skipped entirely (the faithful unavailable arm).
    if rag().is_some() {
        // UNREACHABLE: the disabled singleton never yields a manager. The Python
        // best-effort arm is `try: rag.remove_directory(directory) except: warn`.
        unreachable!("rag_singleton::get_rag_manager() is disabled and returns None")
    }

    Ok(Json(json!({
        "success": true,
        "message": format!("Successfully removed {directory} from RAG index"),
        "directory": directory,
    }))
    .into_response())
}

/// `POST /api/personal/upload` — "Upload files directly into RAG. Supports text and
/// PDF."
///
/// This handler has NO auth dependency in Python (only `get_current_user(request)`
/// to stamp per-chunk `owner` metadata). On the live path `rag` is `None`, so it
/// always raises `503 "RAG system is not available — is the embedding service
/// running?"` — the faithful unavailable arm. The file-write + chunk + index loop
/// is the unreachable rag-available branch.
///
/// FastAPI declares `files: List[UploadFile] = File(...)` — a REQUIRED body field
/// validated during request parsing, BEFORE the handler runs. So a request with no
/// multipart `files` part is a 422 ahead of the 503. We reproduce that order: parse
/// the multipart and require at least one `files` part (422 otherwise), then take
/// the 503 arm.
async fn upload_files_to_rag(mp: Multipart) -> Result<Response, HttpException> {
    // `files: List[UploadFile] = File(...)` — required; a missing field is a 422
    // raised by FastAPI's request validation before the handler body executes.
    let had_files = consume_files_field(mp).await;
    if !had_files {
        return Err(HttpException::new(422, "Field required: files"));
    }

    // user = get_current_user(request)  — read but only used for the (unreachable)
    // per-chunk owner metadata; the 503 arm below never touches it.

    // rag = _rag(); if not rag: raise HTTPException(503, ...)
    // `rag` is always None on the live path.
    match rag() {
        Some(()) => {
            // UNREACHABLE: disabled singleton. The Python arm writes each upload to
            // UPLOADS_DIR, chunks via `rag._split_into_chunks`, and indexes via
            // `rag.add_document`. Not portable to a live call (no manager exists).
            unreachable!("rag_singleton::get_rag_manager() is disabled and returns None")
        }
        None => Err(HttpException::new(
            503,
            "RAG system is not available — is the embedding service running?",
        )),
    }
}

/// `DELETE /api/personal/file` — "Delete a specific file from RAG index and
/// optionally from disk."
///
/// `filepath: str = Query(...)` — required query param. On the live path `rag` is
/// `None`, so `removed_chunks` stays 0 (no `rag.delete_by_source`); the file is
/// deleted from disk iff it sits strictly under `UPLOADS_DIR`, and excluded from the
/// listing (persisted across restarts).
async fn delete_file_from_rag(
    State(s): State<AppState>,
    headers: HeaderMap,
    ci: Option<ConnectInfo<SocketAddr>>,
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<HashMap<String, String>>,
) -> Result<Response, HttpException> {
    let _owner = user_then_admin(&s, &headers, user_str(&user), host_str(&ci).as_deref())?;

    // filepath: str = Query(...)  — required; FastAPI 422 when the param is absent.
    let filepath = match q.get("filepath") {
        Some(f) => f.clone(),
        None => return Err(HttpException::new(422, "Field required: filepath")),
    };

    // try: ... except Exception as e: raise HTTPException(500, f"Failed to delete
    // file: {str(e)}"). The realistic path completes successfully; the one body
    // step that can raise is `os.remove`, whose failure is reproduced below as the
    // route's 500 wrapper.

    // Remove chunks from RAG vector store (best-effort). `rag` is None on the live
    // path, so `removed` stays 0.
    let removed: i64 = 0;
    if rag().is_some() {
        // UNREACHABLE: disabled singleton. Python: `removed = rag.delete_by_source(filepath)`.
        unreachable!("rag_singleton::get_rag_manager() is disabled and returns None")
    }

    // Delete file from disk if it's in uploads dir.
    let mut deleted_from_disk = false;
    let uploads = uploads_dir();
    let abs_target = abspath(&filepath);
    let base_abs = abspath(&uploads);
    // in_uploads = abs_target == base_abs or commonpath([abs_target, base_abs]) == base_abs
    // commonpath raises ValueError on mixed drives / non-comparable paths -> False.
    let in_uploads = abs_target == base_abs
        || common_path(&abs_target, &base_abs)
            .map(|cp| cp == base_abs)
            .unwrap_or(false);
    // if in_uploads and abs_target != base_abs and os.path.exists(abs_target):
    if in_uploads && abs_target != base_abs && Path::new(&abs_target).exists() {
        // os.remove(abs_target). On failure Python raises (e.g. PermissionError),
        // which the outer `except Exception as e` converts into
        // `HTTPException(500, f"Failed to delete file: {str(e)}")`.
        match std::fs::remove_file(&abs_target) {
            Ok(()) => deleted_from_disk = true,
            Err(e) => {
                return Err(HttpException::new(
                    500,
                    format!("Failed to delete file: {e}"),
                ))
            }
        }
    }

    // Exclude the file from the listing (persists across restarts).
    s.personal_docs_manager.exclude_file(&filepath);

    Ok(Json(json!({
        "success": true,
        "removed_chunks": removed,
        "deleted_from_disk": deleted_from_disk,
    }))
    .into_response())
}

// ===========================================================================
// Helpers
// ===========================================================================

/// `_resolve_allowed_personal_dir(directory)` — "Resolve a user-supplied
/// personal-docs path under the allowed root."
///
/// ```python
/// if not directory: raise HTTPException(400, "Directory path is required")
/// # realpath (not abspath) so a symlink inside PERSONAL_DIR that points
/// # outside it is resolved before the commonpath confinement check below;
/// # abspath only normalises `..` and would let such a symlink escape.
/// base_abs = os.path.realpath(PERSONAL_DIR)
/// candidate = directory if os.path.isabs(directory) else os.path.join(base_abs, directory)
/// resolved = os.path.realpath(candidate)
/// try: in_base = os.path.commonpath([resolved, base_abs]) == base_abs
/// except ValueError: in_base = False
/// if not in_base: raise HTTPException(403, "Directory must be inside personal documents")
/// return resolved
/// ```
///
/// ## Symlink-escape fix (parity with upstream Python change)
///
/// The Python function previously used `os.path.abspath` (lexical only); it was
/// updated to `os.path.realpath` so that a symlink inside `PERSONAL_DIR` pointing
/// outside is followed and caught by the `commonpath` containment check.
///
/// Rust's `std::fs::canonicalize` is the `realpath(3)` analogue.  Unlike Python's
/// `os.path.realpath`, which falls back to lexical normalisation for path components
/// that do not exist yet, `canonicalize` returns an error when any component is
/// absent.  We mirror the Python behaviour with `realpath_or_abspath`: try
/// `canonicalize`; if the path (or a component of it) does not exist, fall back to
/// the lexical `abspath`.  This means:
///
/// * **Existing path with a symlink** → symlinks are followed → escape detected.
/// * **Non-existent path** → lexical normalisation only (same as old Python `abspath`,
///   same as new Python `realpath` on a non-existent tail) → `..` escapes are still
///   caught, symlink escapes cannot exist because the link itself doesn't exist.
fn resolve_allowed_personal_dir(directory: &str) -> Result<String, HttpException> {
    // if not directory: raise HTTPException(400, "Directory path is required")
    if directory.is_empty() {
        return Err(HttpException::new(400, "Directory path is required"));
    }
    // base_abs = os.path.realpath(PERSONAL_DIR)
    let base_abs = realpath_or_abspath(&PERSONAL_DIR);
    // candidate = directory if isabs(directory) else join(base_abs, directory)
    let candidate = if is_absolute(directory) {
        directory.to_string()
    } else {
        crate::pyos::path::join(&base_abs, directory)
    };
    // resolved = os.path.realpath(candidate)
    let resolved = realpath_or_abspath(&candidate);
    // in_base = commonpath([resolved, base_abs]) == base_abs  (ValueError -> False)
    let in_base = common_path(&resolved, &base_abs)
        .map(|cp| cp == base_abs)
        .unwrap_or(false);
    if !in_base {
        return Err(HttpException::new(
            403,
            "Directory must be inside personal documents",
        ));
    }
    Ok(resolved)
}

/// Drain a `multipart/form-data` body, returning `true` iff at least one `files`
/// part was present (FastAPI `files: List[UploadFile] = File(...)` requires the
/// field). We read each field's bytes so the body is fully consumed even when we
/// only need the presence flag.
async fn consume_files_field(mut mp: Multipart) -> bool {
    let mut had = false;
    while let Ok(Some(field)) = mp.next_field().await {
        let is_files = field.name() == Some("files");
        // Drain the part body regardless of name.
        let _ = field.bytes().await;
        if is_files {
            had = true;
        }
    }
    had
}

/// `os.path.abspath(p)` — `normpath(join(getcwd(), p))`: make `p` absolute by
/// prepending the CWD when relative, then **lexically** collapse `.` and `..`
/// components WITHOUT resolving symlinks (so a non-existent path still resolves).
///
/// CRITICAL: this must collapse `..`, exactly like CPython's `posixpath.normpath`.
/// `std::path::absolute` deliberately PRESERVES `..` (to keep meaning across
/// symlinks), which would let `PERSONAL_DIR/../../etc` keep `PERSONAL_DIR` as a
/// literal component and slip past the `commonpath`-in-base check — a traversal
/// hole. We therefore normalise the components ourselves.
fn abspath(p: &str) -> String {
    let raw = Path::new(p);
    // join(getcwd(), p) when relative.
    let joined: PathBuf = if raw.is_absolute() {
        raw.to_path_buf()
    } else {
        match std::env::current_dir() {
            Ok(cwd) => cwd.join(raw),
            Err(_) => raw.to_path_buf(),
        }
    };
    normpath(&joined)
}

/// `os.path.normpath`-style lexical normalisation: collapse `.` and `..`
/// components (a `..` pops the previous normal component; a leading-root `..` is
/// discarded, matching POSIX `normpath`). No filesystem access, no symlink
/// resolution.
fn normpath(p: &Path) -> String {
    let mut out: Vec<std::ffi::OsString> = Vec::new();
    let mut root = String::new();
    for comp in p.components() {
        match comp {
            Component::RootDir => root = "/".to_string(),
            Component::Prefix(pref) => root = pref.as_os_str().to_string_lossy().into_owned(),
            Component::CurDir => {} // "." — drop
            Component::ParentDir => {
                // ".." pops the last normal component; at the root (or with no
                // normal component to pop on a relative path) it is dropped when
                // rooted, kept otherwise — but our inputs are always rooted.
                let can_pop = out.last().map(|c| c != "..").unwrap_or(false);
                if can_pop {
                    out.pop();
                } else if root.is_empty() {
                    out.push(std::ffi::OsString::from(".."));
                }
            }
            Component::Normal(seg) => out.push(seg.to_os_string()),
        }
    }
    let mut result = PathBuf::from(&root);
    for seg in out {
        result.push(seg);
    }
    let s = result.to_string_lossy().into_owned();
    if s.is_empty() {
        ".".to_string()
    } else {
        s
    }
}

/// `os.path.realpath(p)` — resolve symlinks via `realpath(3)`, falling back to the
/// lexical `abspath` when the path (or any component of it) does not exist.
///
/// Python's `os.path.realpath` resolves symlinks in whatever prefix of the path
/// already exists on disk and then appends the remaining non-existent suffix
/// lexically.  `std::fs::canonicalize` (the Rust `realpath(3)` wrapper) is
/// all-or-nothing: it errors when any component is absent.  This helper bridges the
/// two:
///
/// 1. Try `canonicalize` — succeeds for fully-existing paths and follows symlinks.
/// 2. On failure, fall back to `abspath` — no symlink resolution, but `..` is
///    still collapsed (same as old Python `abspath` / new Python `realpath` on a
///    non-existent tail).
///
/// The security property we need: a symlink that EXISTS inside `PERSONAL_DIR` and
/// points outside is followed and detected by the containment check.  That requires
/// only step 1 to succeed for such a path, which it will (the symlink file itself
/// exists).
fn realpath_or_abspath(p: &str) -> String {
    match std::fs::canonicalize(p) {
        Ok(resolved) => resolved.to_string_lossy().into_owned(),
        Err(_) => abspath(p),
    }
}

/// `os.path.isabs(p)`.
fn is_absolute(p: &str) -> bool {
    Path::new(p).is_absolute()
}

/// `os.path.commonpath([a, b])` — the longest common leading PATH (component-wise,
/// not string-prefix). Returns `None` for the cases CPython raises `ValueError`
/// on: an empty input or a mix of absolute and relative paths. Both `a` and `b`
/// here are already `abspath`-normalised (so absolute), matching the Python call
/// sites which pass two `os.path.abspath(...)` results.
///
/// CPython compares by path components, so `/a/personal` and `/a/personal_docs`
/// share only `/a` (NOT a naive string prefix) — this reproduces that exactly.
fn common_path(a: &str, b: &str) -> Option<String> {
    let pa = PathBuf::from(a);
    let pb = PathBuf::from(b);
    // ValueError when one is absolute and the other is not.
    if pa.is_absolute() != pb.is_absolute() {
        return None;
    }
    let comps_a: Vec<Component<'_>> = pa.components().collect();
    let comps_b: Vec<Component<'_>> = pb.components().collect();
    let mut common = PathBuf::new();
    let mut any = false;
    for (ca, cb) in comps_a.iter().zip(comps_b.iter()) {
        if ca == cb {
            common.push(ca.as_os_str());
            any = true;
        } else {
            break;
        }
    }
    if any {
        Some(common.to_string_lossy().into_owned())
    } else {
        // No shared component at all — CPython would still return the empty common
        // path for two relative paths, but for two absolute paths the root `/` is
        // always shared, so this branch is effectively unreachable for our
        // abspath'd inputs. Return None to be safe (treated as "not in base").
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rag_is_always_none_live_path() {
        // `_rag()` mirrors the disabled singleton: always None.
        assert!(rag().is_none());
    }

    #[test]
    fn uploads_dir_under_base_data() {
        // `os.path.join(BASE_DIR, "data", "personal_uploads")`.
        let u = uploads_dir();
        assert!(u.ends_with("personal_uploads"));
        assert!(u.contains("data"));
    }

    #[test]
    fn directory_request_parses() {
        let r: DirectoryRequest =
            serde_json::from_value(json!({"directory": "/a/b"})).unwrap();
        assert_eq!(r.directory, "/a/b");
    }

    #[test]
    fn resolve_rejects_empty() {
        let e = resolve_allowed_personal_dir("").unwrap_err();
        assert_eq!(e.status_code, 400);
        assert_eq!(e.detail, "Directory path is required");
    }

    #[test]
    fn resolve_accepts_relative_under_personal_dir() {
        // A relative path is joined onto abspath(PERSONAL_DIR) and stays in-base.
        let resolved = resolve_allowed_personal_dir("subdir").unwrap();
        let base = abspath(&PERSONAL_DIR);
        assert!(common_path(&resolved, &base).as_deref() == Some(base.as_str()));
        assert!(resolved.ends_with("subdir"));
    }

    #[test]
    fn resolve_rejects_outside_personal_dir() {
        // An absolute path outside PERSONAL_DIR -> 403.
        let e = resolve_allowed_personal_dir("/etc/passwd").unwrap_err();
        assert_eq!(e.status_code, 403);
        assert_eq!(e.detail, "Directory must be inside personal documents");
    }

    #[test]
    fn resolve_rejects_dotdot_escape() {
        // PERSONAL_DIR/../../etc escapes the base -> 403 (commonpath != base).
        let base = abspath(&PERSONAL_DIR);
        let escape = format!("{base}/../../../../etc");
        // Pass as absolute so it isn't re-joined under base.
        let e = resolve_allowed_personal_dir(&escape).unwrap_err();
        assert_eq!(e.status_code, 403);
    }

    #[test]
    fn common_path_is_component_wise_not_string_prefix() {
        // `/a/personal` and `/a/personal_docs` share only `/a`, NOT `/a/personal`
        // (the string-prefix trap commonpath avoids).
        assert_eq!(
            common_path("/a/personal", "/a/personal_docs").as_deref(),
            Some("/a")
        );
        assert_eq!(
            common_path("/a/b/c", "/a/b/d").as_deref(),
            Some("/a/b")
        );
        // A file strictly under base shares the full base.
        assert_eq!(
            common_path("/a/b/c/f.txt", "/a/b/c").as_deref(),
            Some("/a/b/c")
        );
    }

    #[test]
    fn common_path_mixed_abs_rel_is_none() {
        // CPython commonpath raises ValueError on a mix of absolute/relative.
        assert!(common_path("/a/b", "a/b").is_none());
    }

    #[test]
    fn router_mounts_all_paths() {
        // The factory yields a `Router<AppState>` mergeable with the inline subset
        // (no duplicate method+path); all six personal paths are disjoint.
        let base: Router<AppState> = Router::new();
        let _merged: Router<AppState> = base.merge(setup_personal_routes());
    }

    #[test]
    fn is_absolute_matches_os_path_isabs() {
        assert!(is_absolute("/abs/path"));
        assert!(!is_absolute("rel/path"));
        assert!(!is_absolute("./rel"));
    }

    // -----------------------------------------------------------------------
    // realpath_or_abspath
    // -----------------------------------------------------------------------

    #[test]
    fn realpath_or_abspath_nonexistent_falls_back_to_abspath() {
        // A path that cannot possibly exist: realpath_or_abspath must not panic
        // and must return the lexically-normalised absolute path.
        let p = "/nonexistent_odysseus_test_path_xyz/a/b/../c";
        let result = realpath_or_abspath(p);
        // Should be the lexical normalisation (no `..` in result).
        assert!(result.starts_with('/'));
        assert!(!result.contains(".."));
        assert!(result.ends_with("/c"), "got: {result}");
    }

    #[test]
    fn realpath_or_abspath_resolves_existing_canonical_path() {
        // For a path that actually exists (e.g. /tmp on macOS -> /private/tmp via
        // symlink, or just /tmp on Linux), canonicalize follows symlinks.  We use
        // the current dir which always exists.
        let cwd = std::env::current_dir().unwrap();
        let cwd_str = cwd.to_string_lossy().to_string();
        let result = realpath_or_abspath(&cwd_str);
        // Must be absolute.
        assert!(result.starts_with('/'));
        // canonicalize resolves any symlinks in CWD itself; the result must be
        // the canonical form of the same directory.
        let canon = std::fs::canonicalize(&cwd).unwrap().to_string_lossy().to_string();
        assert_eq!(result, canon);
    }

    #[test]
    fn resolve_rejects_symlink_escape() {
        // Create a temp dir structure:
        //   <tmp>/base/          <- PERSONAL_DIR substitute
        //   <tmp>/outside/       <- the escape target outside base
        //   <tmp>/base/link      <- symlink pointing to ../outside
        //
        // With the old `abspath` logic, `resolve_allowed_personal_dir` would
        // accept `<tmp>/base/link` because its lexical path stays under base.
        // With the new `realpath_or_abspath` logic it is canonicalized to
        // `<tmp>/outside` which is NOT under base, so it must be rejected (403).
        use std::os::unix::fs::symlink;
        use tempfile::TempDir;

        let tmp = TempDir::new().unwrap();
        let base = tmp.path().join("base");
        let outside = tmp.path().join("outside");
        std::fs::create_dir_all(&base).unwrap();
        std::fs::create_dir_all(&outside).unwrap();

        let link = base.join("link");
        symlink(&outside, &link).unwrap();

        // The link itself exists inside `base`, so canonicalize resolves it to
        // `outside`, which is not under `base`.
        let base_canon = std::fs::canonicalize(&base).unwrap().to_string_lossy().to_string();
        let link_str = link.to_string_lossy().to_string();

        // We call the internal helper directly instead of patching PERSONAL_DIR.
        // Verify: realpath_or_abspath of the link resolves to outside.
        let resolved = realpath_or_abspath(&link_str);
        let outside_canon = std::fs::canonicalize(&outside).unwrap().to_string_lossy().to_string();
        assert_eq!(resolved, outside_canon, "symlink must be followed by realpath_or_abspath");

        // Verify: the commonpath check correctly rejects the resolved path.
        let in_base = common_path(&resolved, &base_canon)
            .map(|cp| cp == base_canon)
            .unwrap_or(false);
        assert!(!in_base, "symlink pointing outside base must NOT be considered in-base");
    }
}
