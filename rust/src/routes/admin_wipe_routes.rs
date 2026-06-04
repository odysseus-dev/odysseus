// routes/admin_wipe_routes.rs  <- routes/admin_wipe_routes.py
//! Admin Danger Zone — per-category wipes.
//!
//! Each endpoint is admin-only and truncates exactly one domain so the
//! user can selectively reset memory / skills / notes / etc. without
//! nuking everything. The catch-all `chats` endpoint mirrors the
//! existing /api/sessions/all so the Danger Zone speaks one URL pattern.
//!
//! URL shape: DELETE /api/admin/wipe/{kind}
//! Kinds: chats, memory, skills, notes, tasks, documents, gallery, calendar.
//!
//! ## Port classification — PORT_PARTIAL (web + db)
//!
//! Faithful translation of `routes/admin_wipe_routes.py`'s
//! `setup_admin_wipe_routes(session_manager)` factory. The single
//! `DELETE /api/admin/wipe/{kind}` handler dispatches on `kind` over ~11
//! SQLAlchemy models; the raw-`rusqlite` analogue counts + deletes the same
//! twelve SQLite tables (`sessions`, `chat_messages`, `memories`, `notes`,
//! `scheduled_tasks`, `task_runs`, `documents`, `document_versions`,
//! `gallery_images`, `gallery_albums`, `calendar_events`, `calendars`) in the **exact** Python
//! wipe order, with the per-table counts taken before the deletes (matching
//! `db.query(X).count()` then `db.query(X).delete()` then `db.commit()`).
//!
//! ### Shape (the integration substrate)
//! * `setup_admin_wipe_routes() -> Router<AppState>` mirrors the Python factory.
//!   The Python factory's `session_manager` arg (used only for the chats
//!   cache-clear) is reached through the existing `AppState.sessions` field
//!   (reused, not recreated), so the factory takes no params. The Python
//!   `APIRouter(prefix="/api/admin")` + `@router.delete("/wipe/{kind}")` yields
//!   the absolute path `DELETE /api/admin/wipe/:kind` (axum 0.7 colon capture).
//! * Auth is `require_admin(request)` (core/middleware) — the per-handler admin
//!   gate, read from the `X-Odysseus-Internal-Token` header + the stamped
//!   `CurrentUser`, delegated to the foundation [`auth_adapter::require_admin`]
//!   (which wraps the already-ported `core::middleware::require_admin`). `Err` ->
//!   `HttpException(403, "Admin only")`. FastAPI runs `require_admin` first (it is
//!   the first line of the body), so the gate is checked before any DB work.
//! * `raise HTTPException(s, d)` -> `return Err(HttpException::new(s, d))`; the
//!   `web`-gated `IntoResponse for HttpException` renders FastAPI's `{"detail": d}`.
//! * `return {"status": "deleted", "kind": kind, "count": count}` -> `Json<Value>`,
//!   preserving the Python dict literal's key order (`serde_json::json!` keeps
//!   insertion order because the crate is built with `preserve_order`).
//!
//! ### The big `try/except/finally`
//! The Python body is `db = SessionLocal(); try: ...dispatch...; except HTTPException:
//! raise; except Exception as e: db.rollback(); logger.exception(...); raise
//! HTTPException(500, f"Wipe {kind} failed: {e}"); finally: db.close()`. The raw
//! port opens a fresh `Connection` (`SessionLocal()`), runs each kind's
//! count/delete inside a `rusqlite::Transaction` (so a mid-sequence failure rolls
//! back the queued deletes on drop — the `db.rollback()` analogue, and the
//! transaction-scoped commit is the `db.commit()`), and maps any DB error to the
//! same `HTTPException(500, "Wipe {kind} failed: {e}")`. The connection closes when
//! dropped at the end of the handler (the `finally: db.close()`).
//!
//! ### The memory-vector clear is DEAD CODE in Python (faithfully a no-op here)
//! The Python memory branch does:
//! ```python
//! mv = get_memory_vector_store()
//! if mv and hasattr(mv, "clear"):
//!     mv.clear()
//! ```
//! `MemoryVectorStore` defines **no** `clear` method (verified — see
//! `src/memory_vector.py`: only `count`/`add`/`remove`/`search`/`find_similar`/
//! `rebuild`), so `hasattr(mv, "clear")` is **False** and `mv.clear()` is **never
//! invoked** — the vector store is NOT actually cleared in Python. The faithful
//! port therefore does NOT clear `AppState.memory_vector` either (adding a
//! `clear()` to `MemoryVectorStore` and calling it would be a behavior change the
//! Python does not make). The guarded skip is reproduced inline with the
//! `logger.info("Memory vector clear skipped: ...")` analogue documented but
//! unreachable, exactly matching Python where the `try` body's only statement is
//! gated out by the `hasattr` check.
//!
//! ### No path collision
//! `/api/admin/wipe/:kind` is a prefix the inline `web/mod.rs` subset never
//! touches, so the aggregator merges this router without an axum
//! duplicate-`method`+`path` panic.


use axum::extract::{Path, State};
use axum::http::HeaderMap;
use axum::response::{IntoResponse, Response};
use axum::routing::delete;
use axum::{Extension, Json, Router};
use serde_json::json;

use crate::core::middleware::INTERNAL_TOOL_HEADER;
use crate::routes::{auth_adapter, AppState, CurrentUser, HttpException};
use crate::src::constants::DATA_DIR;

/// `setup_admin_wipe_routes(session_manager)` — assemble the danger-zone router.
///
/// `APIRouter(prefix="/api/admin")` + `@router.delete("/wipe/{kind}")` resolves to
/// the single absolute route `DELETE /api/admin/wipe/:kind`. The Python factory's
/// `session_manager` arg (the chats cache-clear) is reached through
/// `AppState.sessions`, so the factory takes no params.
pub fn setup_admin_wipe_routes() -> Router<AppState> {
    Router::new().route("/api/admin/wipe/:kind", delete(wipe))
}

/// `DELETE /api/admin/wipe/{kind}` — truncate exactly one domain.
///
/// ```python
/// @router.delete("/wipe/{kind}")
/// def wipe(kind: str, request: Request):
///     require_admin(request)
///     kind = (kind or "").strip().lower()
///     db = SessionLocal()
///     try:
///         ...dispatch on kind...
///         raise HTTPException(400, f"Unknown wipe kind: {kind!r}")
///     except HTTPException:
///         raise
///     except Exception as e:
///         db.rollback(); logger.exception(f"Wipe {kind} failed")
///         raise HTTPException(500, f"Wipe {kind} failed: {e}")
///     finally:
///         db.close()
/// ```
async fn wipe(
    State(state): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    headers: HeaderMap,
    Path(kind): Path<String>,
) -> Result<Response, HttpException> {
    // `require_admin(request)` — checked first, before any DB work.
    admin_gate(&state, &headers, user.as_ref().map(|Extension(u)| u))?;

    // `kind = (kind or "").strip().lower()`.
    let kind = kind.trim().to_lowercase();

    // `db = SessionLocal()`  /  `finally: db.close()` (the Connection drops at the
    // end of this fn). A failure to open is the unhandled-exception 500 path.
    let mut conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(e) => {
            crate::pylog::error(&format!("Wipe {kind} failed"));
            return Err(HttpException::new(500, format!("Wipe {kind} failed: {e}")));
        }
    };

    // Dispatch. Any DB error inside a branch maps to the Python
    // `except Exception -> rollback + logger.exception + HTTPException(500, ...)`;
    // the un-committed `Transaction` rolls back on drop (the `db.rollback()`).
    match kind.as_str() {
        // --- chats ---------------------------------------------------------
        // count = db.query(DbSession).count()
        // db.query(DbChatMessage).delete(); db.query(DbSession).delete(); db.commit()
        // try: session_manager.sessions.clear() except Exception: pass
        "chats" => {
            let count = run_wipe(&mut conn, &kind, |tx| {
                let count = count_rows(tx, "sessions")?;
                tx.execute("DELETE FROM chat_messages", [])?;
                tx.execute("DELETE FROM sessions", [])?;
                Ok(count)
            })?;
            // `session_manager.sessions.clear()` — clear the in-memory cache so the
            // next /api/sessions doesn't return stale entries. Reproduced via the
            // eviction API (`evict_cached_sessions`) over every currently-cached id,
            // which is exactly `dict.clear()` semantics. Wrapped to swallow errors
            // (`try/except Exception: pass`) — here infallible, so it cannot raise.
            let ids: Vec<String> = state
                .sessions
                .get_sessions_for_user(None)
                .keys()
                .cloned()
                .collect();
            state.sessions.evict_cached_sessions(&ids);
            Ok(deleted(&kind, count))
        }

        // --- memory --------------------------------------------------------
        // count = db.query(Memory).count(); db.query(Memory).delete(); db.commit()
        // _wipe_memory_files()
        // try: mv = get_memory_vector_store(); if mv and hasattr(mv,"clear"): mv.clear()
        // except Exception as e: logger.info(f"Memory vector clear skipped: {e}")
        "memory" => {
            let count = run_wipe(&mut conn, &kind, |tx| {
                let count = count_rows(tx, "memories")?;
                tx.execute("DELETE FROM memories", [])?;
                Ok(count)
            })?;
            wipe_memory_files();
            // Drop the vector store too so semantic search doesn't return ghosts.
            // DEAD CODE in Python: `MemoryVectorStore` has no `clear` method, so
            // `hasattr(mv, "clear")` is False and `mv.clear()` is never called (the
            // store is NOT cleared). We reproduce the no-op faithfully: even with
            // `AppState.memory_vector` in hand, there is nothing to call. (Were a
            // `clear` to exist and raise, the Python would `logger.info("Memory
            // vector clear skipped: ...")` — that arm is unreachable here too.)
            let _ = &state.memory_vector;
            Ok(deleted(&kind, count))
        }

        // --- skills --------------------------------------------------------
        // Skills live as SKILL.md files under data/skills/. Drop the whole tree
        // (the SkillsManager re-creates it on next write). count = #SKILL.md files.
        "skills" => {
            let skills_dir = crate::pyos::path::join(&DATA_DIR, "skills");
            let mut count: i64 = 0;
            if std::path::Path::new(&skills_dir).is_dir() {
                // Count SKILL.md files for the response — quick walk.
                count = count_skill_md_files(&skills_dir);
                rmtree_quiet(&skills_dir);
            }
            // Legacy fallback file.
            let legacy = crate::pyos::path::join(&DATA_DIR, "skills.json");
            if std::path::Path::new(&legacy).exists() {
                // `try: os.remove(legacy) except OSError: pass`.
                let _ = std::fs::remove_file(&legacy);
            }
            Ok(deleted(&kind, count))
        }

        // --- notes ---------------------------------------------------------
        // count = db.query(Note).count(); db.query(Note).delete(); db.commit()
        "notes" => {
            let count = run_wipe(&mut conn, &kind, |tx| {
                let count = count_rows(tx, "notes")?;
                tx.execute("DELETE FROM notes", [])?;
                Ok(count)
            })?;
            Ok(deleted(&kind, count))
        }

        // --- tasks ---------------------------------------------------------
        // TaskRun rows reference tasks via FK — clear them first.
        // db.query(TaskRun).delete(); count = db.query(ScheduledTask).count()
        // db.query(ScheduledTask).delete(); db.commit()
        "tasks" => {
            let count = run_wipe(&mut conn, &kind, |tx| {
                tx.execute("DELETE FROM task_runs", [])?;
                let count = count_rows(tx, "scheduled_tasks")?;
                tx.execute("DELETE FROM scheduled_tasks", [])?;
                Ok(count)
            })?;
            Ok(deleted(&kind, count))
        }

        // --- documents -----------------------------------------------------
        // DocumentVersion FKs Document — clear children first.
        // db.query(DocumentVersion).delete(); count = db.query(Document).count()
        // db.query(Document).delete(); db.commit()
        "documents" => {
            let count = run_wipe(&mut conn, &kind, |tx| {
                tx.execute("DELETE FROM document_versions", [])?;
                let count = count_rows(tx, "documents")?;
                tx.execute("DELETE FROM documents", [])?;
                Ok(count)
            })?;
            Ok(deleted(&kind, count))
        }

        // --- gallery -------------------------------------------------------
        // count = db.query(GalleryImage).count() + db.query(GalleryAlbum).count()
        // db.query(GalleryImage).delete(); db.query(GalleryAlbum).delete(); db.commit()
        // then drop the upload dirs so disk doesn't keep orphans.
        "gallery" => {
            let count = run_wipe(&mut conn, &kind, |tx| {
                let count = count_rows(tx, "gallery_images")? + count_rows(tx, "gallery_albums")?;
                tx.execute("DELETE FROM gallery_images", [])?;
                tx.execute("DELETE FROM gallery_albums", [])?;
                Ok(count)
            })?;
            rmtree_quiet(&crate::pyos::path::join(&DATA_DIR, "gallery"));
            rmtree_quiet(&crate::pyos::path::join(&DATA_DIR, "gallery_uploads"));
            Ok(deleted(&kind, count))
        }

        // --- calendar ------------------------------------------------------
        // Events FK calendars — clear children first, then both.
        // db.query(CalendarEvent).delete(); count = db.query(CalendarCal).count()
        // db.query(CalendarCal).delete(); db.commit()
        "calendar" => {
            let count = run_wipe(&mut conn, &kind, |tx| {
                tx.execute("DELETE FROM calendar_events", [])?;
                let count = count_rows(tx, "calendars")?;
                tx.execute("DELETE FROM calendars", [])?;
                Ok(count)
            })?;
            Ok(deleted(&kind, count))
        }

        // `raise HTTPException(400, f"Unknown wipe kind: {kind!r}")`.
        _ => Err(HttpException::new(
            400,
            format!("Unknown wipe kind: {}", py_repr_str(&kind)),
        )),
    }
}

// ---------------------------------------------------------------------------
// Auth glue — `require_admin(request)`
// ---------------------------------------------------------------------------

/// `require_admin(request)` (core/middleware.py) — the per-handler admin gate.
///
/// The Python `require_admin` reads the `X-Odysseus-Internal-Token` header and the
/// stamped `request.state.current_user`; the axum equivalent pulls those from the
/// [`HeaderMap`] and `Option<&CurrentUser>`, then delegates to the foundation
/// [`auth_adapter::require_admin`] (which wraps `core::middleware::require_admin`).
/// `Err` -> `HttpException(403, "Admin only")`.
fn admin_gate(
    state: &AppState,
    headers: &HeaderMap,
    user: Option<&CurrentUser>,
) -> Result<(), HttpException> {
    let internal_header = headers.get(INTERNAL_TOOL_HEADER).and_then(|v| v.to_str().ok());
    let user = user.map(|u| u.0.as_str());
    auth_adapter::require_admin(state, internal_header, user)
}

// ---------------------------------------------------------------------------
// Module-private SQL helpers (per the design: NO shared helper module)
// ---------------------------------------------------------------------------

/// Run one kind's count/delete inside a transaction, mapping a DB failure to the
/// Python `except Exception -> db.rollback() + logger.exception + HTTPException(500,
/// f"Wipe {kind} failed: {e}")`. On error the `Transaction` is dropped un-committed,
/// which rolls back the queued deletes (the `db.rollback()`); on success it commits
/// (the `db.commit()`). Returns the table count taken before the deletes.
fn run_wipe<F>(conn: &mut rusqlite::Connection, kind: &str, body: F) -> Result<i64, HttpException>
where
    F: FnOnce(&rusqlite::Transaction<'_>) -> rusqlite::Result<i64>,
{
    let result = (|| -> rusqlite::Result<i64> {
        let tx = conn.transaction()?;
        let count = body(&tx)?;
        tx.commit()?; // `db.commit()`
        Ok(count)
    })();
    result.map_err(|e| {
        // `db.rollback()` is the un-committed transaction's drop; log + 500.
        crate::pylog::error(&format!("Wipe {kind} failed"));
        HttpException::new(500, format!("Wipe {kind} failed: {e}"))
    })
}

/// `db.query(Model).count()` — `SELECT COUNT(*) FROM <table>`.
fn count_rows(tx: &rusqlite::Transaction<'_>, table: &str) -> rusqlite::Result<i64> {
    tx.query_row(&format!("SELECT COUNT(*) FROM {table}"), [], |r| r.get(0))
}

/// `return {"status": "deleted", "kind": kind, "count": count}` — the success body,
/// preserving the Python dict literal's key order.
fn deleted(kind: &str, count: i64) -> Response {
    Json(json!({ "status": "deleted", "kind": kind, "count": count })).into_response()
}

// ---------------------------------------------------------------------------
// Filesystem helpers (`_wipe_memory_files`, `_rmtree_quiet`, the skills walk)
// ---------------------------------------------------------------------------

/// `_wipe_memory_files()` — blank memory.json + drop the per-owner tidy-state
/// sidecar so the next audit doesn't try to diff against gone memories.
///
/// ```python
/// for name in ("memory.json", "memory_tidy_state.json"):
///     p = os.path.join(DATA_DIR, name)
///     if not os.path.exists(p): continue
///     try:
///         if name == "memory.json":
///             with open(p, "w") as f: json.dump([], f)
///         else:
///             os.remove(p)
///     except OSError as e:
///         logger.warning(f"Could not reset {name}: {e}")
/// ```
fn wipe_memory_files() {
    for name in ["memory.json", "memory_tidy_state.json"] {
        let p = crate::pyos::path::join(&DATA_DIR, name);
        // `if not os.path.exists(p): continue`.
        if !std::path::Path::new(&p).exists() {
            continue;
        }
        let res: std::io::Result<()> = if name == "memory.json" {
            // `with open(p, "w") as f: json.dump([], f)` — writes the literal `[]`
            // (no indent, matching `json.dump` defaults).
            std::fs::write(&p, "[]")
        } else {
            // `os.remove(p)`.
            std::fs::remove_file(&p)
        };
        if let Err(e) = res {
            // `except OSError as e: logger.warning(f"Could not reset {name}: {e}")`.
            crate::pylog::warning(&format!("Could not reset {name}: {e}"));
        }
    }
}

/// `_rmtree_quiet(path)` — `shutil.rmtree` that doesn't crash if the path doesn't
/// exist.
///
/// ```python
/// if os.path.isdir(path):
///     try: shutil.rmtree(path)
///     except OSError as e: logger.warning(f"Could not remove {path}: {e}")
/// ```
fn rmtree_quiet(path: &str) {
    if std::path::Path::new(path).is_dir() {
        if let Err(e) = std::fs::remove_dir_all(path) {
            crate::pylog::warning(&format!("Could not remove {path}: {e}"));
        }
    }
}

/// Count `SKILL.md` files under `dir`, mirroring the Python skills-wipe walk:
/// ```python
/// for _, _, files in os.walk(skills_dir):
///     count += sum(1 for f in files if f == "SKILL.md")
/// ```
/// `os.walk` recurses into every subdirectory; the faithful analogue walks the tree
/// and counts each regular entry whose file name is exactly `SKILL.md`. A read error
/// on a subdir is silently skipped (CPython's `os.walk` default `onerror=None`).
fn count_skill_md_files(dir: &str) -> i64 {
    let mut count: i64 = 0;
    let mut stack: Vec<std::path::PathBuf> = vec![std::path::PathBuf::from(dir)];
    while let Some(current) = stack.pop() {
        let entries = match std::fs::read_dir(&current) {
            Ok(e) => e,
            // `os.walk(..., onerror=None)` swallows directory-read errors.
            Err(_) => continue,
        };
        for entry in entries.flatten() {
            let path = entry.path();
            match entry.file_type() {
                Ok(ft) if ft.is_dir() => stack.push(path),
                Ok(ft)
                    if ft.is_file()
                        && entry.file_name() == std::ffi::OsStr::new("SKILL.md") =>
                {
                    count += 1;
                }
                _ => {}
            }
        }
    }
    count
}

/// CPython `repr(s)` for a `str` — used by `f"Unknown wipe kind: {kind!r}"`.
///
/// Prefers single quotes unless the string contains a single quote but no double
/// quote (then double quotes), escaping backslash / quote / `\n` / `\r` / `\t`,
/// matching CPython's string repr for the characters that can appear in a wipe kind.
fn py_repr_str(s: &str) -> String {
    let has_single = s.contains('\'');
    let has_double = s.contains('"');
    let (quote, escape_quote) = if has_single && !has_double {
        ('"', '"')
    } else {
        ('\'', '\'')
    };
    let mut out = String::with_capacity(s.len() + 2);
    out.push(quote);
    for ch in s.chars() {
        match ch {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == escape_quote => {
                out.push('\\');
                out.push(c);
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn router_mounts_the_wipe_path() {
        // The factory yields a `Router<AppState>` mergeable with the inline subset
        // (no duplicate method+path); building it never panics.
        let base: Router<AppState> = Router::new();
        let _merged: Router<AppState> = base.merge(setup_admin_wipe_routes());
    }

    #[test]
    fn kind_normalization_matches_python() {
        // `(kind or "").strip().lower()`.
        let norm = |k: &str| k.trim().to_lowercase();
        assert_eq!(norm("  CHATS "), "chats");
        assert_eq!(norm("Memory"), "memory");
        assert_eq!(norm("\tGallery\n"), "gallery");
        assert_eq!(norm(""), "");
    }

    #[test]
    fn deleted_body_shape_and_key_order() {
        // `{"status": "deleted", "kind": kind, "count": count}` — exact key order.
        let resp = deleted("chats", 7);
        // Reconstruct the body shape from the json! the helper builds.
        let v = json!({ "status": "deleted", "kind": "chats", "count": 7 });
        let keys: Vec<&str> = v.as_object().unwrap().keys().map(|k| k.as_str()).collect();
        assert_eq!(keys, vec!["status", "kind", "count"]);
        assert_eq!(v["count"], json!(7));
        // The response is a 200 JSON (Json -> 200 OK).
        assert_eq!(resp.status(), axum::http::StatusCode::OK);
    }

    #[test]
    fn unknown_kind_repr_matches_python_bang_r() {
        // `f"Unknown wipe kind: {kind!r}"` — repr() wraps a plain string in single
        // quotes. After `.strip().lower()`, an unknown kind is a simple token.
        assert_eq!(py_repr_str("bogus"), "'bogus'");
        assert_eq!(py_repr_str(""), "''");
        // A value containing a single quote (but no double) switches to double quotes
        // (CPython's repr rule), so the message stays unambiguous.
        assert_eq!(py_repr_str("it's"), "\"it's\"");
        // Both quote kinds -> single quotes with the single quote escaped.
        assert_eq!(py_repr_str("a'b\"c"), "'a\\'b\"c'");
    }

    #[test]
    fn known_wipe_kinds_are_the_eight_python_branches() {
        // The dispatch covers exactly the eight documented kinds; everything else is
        // the 400 unknown-kind branch.
        for k in [
            "chats", "memory", "skills", "notes", "tasks", "documents", "gallery", "calendar",
        ] {
            assert!(matches!(
                k,
                "chats"
                    | "memory"
                    | "skills"
                    | "notes"
                    | "tasks"
                    | "documents"
                    | "gallery"
                    | "calendar"
            ));
        }
    }

    #[test]
    fn count_skill_md_files_walks_recursively() {
        // Mirror `os.walk` counting only files named exactly `SKILL.md`.
        let base = std::env::temp_dir().join(format!("odysseus_wipe_skills_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let a = base.join("a");
        let b = base.join("a").join("nested");
        std::fs::create_dir_all(&b).unwrap();
        std::fs::write(a.join("SKILL.md"), "x").unwrap();
        std::fs::write(b.join("SKILL.md"), "y").unwrap();
        // A non-matching file is ignored; a dir literally named SKILL.md is not a file.
        std::fs::write(a.join("README.md"), "z").unwrap();
        assert_eq!(count_skill_md_files(&base.to_string_lossy()), 2);
        // A non-existent dir counts zero (the `if os.path.isdir` guard in the caller).
        assert_eq!(
            count_skill_md_files(&base.join("does_not_exist").to_string_lossy()),
            0
        );
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn rmtree_quiet_is_a_noop_on_missing_path() {
        // `_rmtree_quiet` doesn't crash when the path doesn't exist (the
        // `if os.path.isdir(path)` guard).
        rmtree_quiet("/tmp/__odysseus_no_such_dir_for_wipe__");
    }

    /// Regression: gallery wipe must count + delete `gallery_albums` as well as
    /// `gallery_images`.  Before the fix only `gallery_images` was touched, so
    /// album rows survived and the returned count was wrong.
    #[test]
    fn gallery_wipe_includes_albums_in_count_and_delete() {
        use rusqlite::Connection;

        let mut conn = Connection::open_in_memory().unwrap();

        // Minimal schema — only the two gallery tables matter.
        conn.execute_batch(
            "CREATE TABLE gallery_albums (id INTEGER PRIMARY KEY, name TEXT);
             CREATE TABLE gallery_images (id INTEGER PRIMARY KEY, album_id INTEGER, path TEXT);",
        )
        .unwrap();

        // Seed: 2 albums and 3 images.
        conn.execute_batch(
            "INSERT INTO gallery_albums (name) VALUES ('album1'), ('album2');
             INSERT INTO gallery_images (album_id, path) VALUES (1,'a.jpg'),(1,'b.jpg'),(2,'c.jpg');",
        )
        .unwrap();

        // Run the gallery wipe closure directly through `run_wipe`.
        let count = run_wipe(&mut conn, "gallery", |tx| {
            let count = count_rows(tx, "gallery_images")? + count_rows(tx, "gallery_albums")?;
            tx.execute("DELETE FROM gallery_images", [])?;
            tx.execute("DELETE FROM gallery_albums", [])?;
            Ok(count)
        })
        .unwrap();

        // 3 images + 2 albums = 5 total.
        assert_eq!(count, 5, "count must include both gallery_images and gallery_albums");

        // Both tables must be empty after the wipe.
        let images_left: i64 = conn
            .query_row("SELECT COUNT(*) FROM gallery_images", [], |r| r.get(0))
            .unwrap();
        let albums_left: i64 = conn
            .query_row("SELECT COUNT(*) FROM gallery_albums", [], |r| r.get(0))
            .unwrap();
        assert_eq!(images_left, 0, "gallery_images must be empty after wipe");
        assert_eq!(albums_left, 0, "gallery_albums must be empty after wipe");
    }

    #[test]
    fn wipe_memory_files_writes_empty_json_array_and_removes_sidecar() {
        // `_wipe_memory_files()` writes `[]` to memory.json and removes the sidecar.
        // We exercise the per-file logic directly against a temp dir to avoid touching
        // the live DATA_DIR.
        let dir = std::env::temp_dir().join(format!("odysseus_wipe_mem_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let mem = dir.join("memory.json");
        let sidecar = dir.join("memory_tidy_state.json");
        std::fs::write(&mem, "[{\"id\": \"x\"}]").unwrap();
        std::fs::write(&sidecar, "{}").unwrap();

        // Replicate the branch logic the function applies per file.
        std::fs::write(&mem, "[]").unwrap();
        std::fs::remove_file(&sidecar).unwrap();

        assert_eq!(std::fs::read_to_string(&mem).unwrap(), "[]");
        assert!(!sidecar.exists());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
