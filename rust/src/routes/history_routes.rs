// routes/history_routes.rs  <- routes/history_routes.py
//! History routes — session history, truncation, fork, conversation topics
//! (routes WAVE 4 — the RECONCILIATION).
//!
//! Faithful translation of `routes/history_routes.py`'s `setup_history_routes`
//! factory: `GET /api/history/{session_id}`, the message-CRUD POSTs under
//! `/api/session/{session_id}/...` (truncate / message / delete-messages /
//! edit-message / mark-stopped / update-last-meta / merge-last-assistant / fork),
//! `GET /api/conversations/topics`, and `POST /api/session/{session_id}/compact`.
//!
//! ## SUPERSEDES the inline `web/mod.rs` subset
//! This module OWNS two paths the rest of the codebase otherwise registers:
//! * `GET /api/history/{session_id}` — the inline `web/mod.rs::get_history`
//!   stub (which returned only `{"history": [...]}`). The full Python returns the
//!   richer `{history, model, endpoint_url, name}` shape + the hidden-message skip
//!   + the DB fallback, all reproduced here. The reconcile step deletes the inline
//!   route in the same commit this module is mounted.
//! * `POST /api/session/{session_id}/compact` — the intra-Python DUPLICATE
//!   (`session_routes.py` also defines a `/compact`; `history_routes` is included
//!   *after* it in `app.py`, so by FastAPI's last-include-wins this module OWNS
//!   `/compact`). It is registered HERE, never in the `session_routes` port.
//!
//! ## Shape (the integration substrate)
//! * `setup_history_routes() -> Router<AppState>` mirrors the Python factory. The
//!   Python `APIRouter(tags=["history"])` has NO prefix, so every handler carries
//!   the absolute path it declares (`/api/history/...`, `/api/session/...`,
//!   `/api/conversations/...`). axum 0.7 path captures use the colon form
//!   (`/:session_id`), never braces.
//! * The `session_manager` factory arg becomes `State<AppState>.sessions`. The
//!   Python mutates the *live* cached `Session` object returned by
//!   `session_manager.get_session(...)`; the Rust `get_session` returns a clone,
//!   so in-memory edits go through [`SessionManager::with_session_mut`] (the
//!   faithful "mutate the cached object under one lock" primitive), preceded by a
//!   `get_session(...)` to reproduce the lazy DB-hydration + `KeyError`-on-miss.
//! * Ownership is `_verify_session_owner` (the auth_adapter
//!   [`verify_session_owner`], STRICT: a `null`-owner row is NOT owned). The
//!   `/api/conversations/topics` handler instead reads `get_current_user`
//!   directly (the load-bearing `None` auth-disabled case).
//! * `raise HTTPException(s, d)` -> `return Err(HttpException::new(s, d))`.
//! * `await request.json()` is the raw untyped body in every POST here, so it is
//!   read from [`axum::body::Bytes`] and parsed by hand, preserving the Python's
//!   try/except ordering (a malformed body falls into the broad `except Exception`
//!   -> 500, NOT a 422, because FastAPI's `request.json()` raises *inside* the
//!   handler `try`).
//!
//! ## Heavy ChatMessage / Session row CRUD -> module-private raw rusqlite
//! Python's delete-by-id / legacy-index delete / edit / compact insert run raw
//! ORM queries over `chat_messages` + `sessions`; the dict-vs-`ChatMessage`
//! duality of `session.history` collapses to `Vec<ChatMessage>` (Rust history is
//! always typed messages, so the `isinstance(msg, dict)` branches are dead and
//! omitted). Each DB mutation is a module-private raw-SQL helper, single-writer.
//!
//! ## Real DB write-back — `mark_stopped` / `update_last_meta` /
//! `merge_last_assistant`
//! Upstream now orders these handlers' `chat_messages` queries by `timestamp`
//! (the column that actually exists), so they perform real DB write-back rather
//! than tripping the historical `AttributeError`-on-`created_at` 500. After the
//! in-memory mutation each handler reaches the DB block:
//! * `mark_stopped` / `update_last_meta` load the LAST assistant row
//!   (`order_by(timestamp.desc()).first()`), merge metadata (`stopped`/`model`
//!   for stop; the request `metadata` for update), and rewrite its `metadata`.
//! * `merge_last_assistant` loads the session's rows ordered by `timestamp`,
//!   rewrites the second-to-last assistant row with the merged content/metadata,
//!   and deletes everything between it and the last assistant row. Its
//!   `len(ai_indices) < 2` early-return (`{"merged": False}`, 200) is still BEFORE
//!   the DB block, so that branch returns 200 without touching the DB.
//!
//! ## No path collision (with the rest of the aggregator)
//! Every route here is under `/api/history/...`, `/api/session/...`, or
//! `/api/conversations/...`. The colliding `session_routes` / inline subset are
//! reconciled in the same commit this module mounts.


use axum::extract::{Path, State};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Extension, Json, Router};
use serde_json::{json, Map, Value};

use crate::core::models::{ChatMessage, Session};
use crate::routes::auth_adapter::verify_session_owner;
use crate::routes::{AppState, CurrentUser, HttpException};

// ---------------------------------------------------------------------------
// Router factory
// ---------------------------------------------------------------------------

/// `def setup_history_routes(session_manager) -> APIRouter`.
///
/// The Python `APIRouter(tags=["history"])` carries no prefix, so the routes are
/// the absolute paths each handler declares. They are registered in the Python
/// source order. This module additionally OWNS `/api/session/:sid/compact` (the
/// intra-Python duplicate that `session_routes` also declares — `history_routes`
/// is `include_router`'d last, so it wins).
pub fn setup_history_routes() -> Router<AppState> {
    Router::new()
        // GET /api/history/{session_id}
        .route("/api/history/:session_id", get(get_session_history))
        // POST /api/session/{session_id}/truncate
        .route("/api/session/:session_id/truncate", post(truncate_session))
        // POST /api/session/{session_id}/message
        .route("/api/session/:session_id/message", post(add_message))
        // POST /api/session/{session_id}/delete-messages
        .route(
            "/api/session/:session_id/delete-messages",
            post(delete_messages),
        )
        // POST /api/session/{session_id}/edit-message
        .route("/api/session/:session_id/edit-message", post(edit_message))
        // POST /api/session/{session_id}/mark-stopped
        .route("/api/session/:session_id/mark-stopped", post(mark_stopped))
        // POST /api/session/{session_id}/update-last-meta
        .route(
            "/api/session/:session_id/update-last-meta",
            post(update_last_meta),
        )
        // POST /api/session/{session_id}/merge-last-assistant
        .route(
            "/api/session/:session_id/merge-last-assistant",
            post(merge_last_assistant),
        )
        // POST /api/session/{session_id}/fork
        .route("/api/session/:session_id/fork", post(fork_session))
        // GET /api/conversations/topics
        .route("/api/conversations/topics", get(get_conversation_topics))
        // POST /api/session/{session_id}/compact  (OWNED here — last-include-wins)
        .route("/api/session/:session_id/compact", post(compact_session))
}

// ---------------------------------------------------------------------------
// GET /api/history/{session_id}
// ---------------------------------------------------------------------------

/// `GET /api/history/{session_id}` — return the session's visible history plus its
/// model / endpoint / name.
///
/// ```python
/// _verify_session_owner(request, session_id)
/// try: session = session_manager.get_session(session_id)
/// except KeyError: raise HTTPException(404, f"Session '{session_id}' not found")
/// history_dict = [...]  # ChatMessage rows, skipping hidden; falls back to DB if empty
/// return {"history": history_dict, "model": ..., "endpoint_url": ..., "name": ...}
/// ```
///
/// SUPERSEDES the inline `web/mod.rs::get_history` (which returned only the
/// `history` key and used the laxer `owns()` predicate). This is the faithful
/// Python: STRICT ownership via `_verify_session_owner`, the hidden-message skip,
/// the DB fallback (with the in-memory write-back), and the four-key return shape.
async fn get_session_history(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(session_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    // `_verify_session_owner(request, session_id)` — 403 (no user) / 404 (missing
    // or not-owned). STRICT: a null-owner row is not owned by anyone.
    verify_session_owner(&s, user.as_deref(), &session_id)?;

    // `try: session = session_manager.get_session(session_id)`
    // `except KeyError: raise HTTPException(404, f"Session '{session_id}' not found")`
    // (note the SINGLE-QUOTED session id in this handler's 404 detail — distinct
    // from `_verify_session_owner`'s unquoted form.)
    let session = s
        .sessions
        .get_session(&session_id)
        .map_err(|_| HttpException::new(404, format!("Session '{session_id}' not found")))?;

    // `history_dict = []` — visible (non-hidden) in-memory messages.
    let mut history_dict: Vec<Value> = Vec::new();
    for msg in &session.history {
        // `if msg.metadata and msg.metadata.get("hidden"): continue` — Python
        // truthiness: a present, non-empty metadata dict with a truthy "hidden".
        if let Some(md) = &msg.metadata {
            if !md.is_empty() && md.get("hidden").map(json_is_truthy).unwrap_or(false) {
                continue;
            }
        }
        // `entry = {"role": ..., "content": ...}; if msg.metadata: entry["metadata"] = ...`
        let mut entry = Map::new();
        entry.insert("role".to_string(), json!(msg.role));
        entry.insert("content".to_string(), json!(msg.content));
        if let Some(md) = &msg.metadata {
            // `if msg.metadata:` — truthy only when present AND non-empty.
            if !md.is_empty() {
                entry.insert("metadata".to_string(), Value::Object(md.clone()));
            }
        }
        history_dict.push(Value::Object(entry));
    }

    // `if not history_dict:` — fall back to the DB (and write the result back into
    // the live cached session's history).
    if history_dict.is_empty() {
        match load_history_from_db(&session_id) {
            Ok(db_history) => {
                // `if db_history: session.history = [ChatMessage(...) for m in db_history]`
                // — rebuild the live cache from the FULL set so hidden messages
                // (e.g. compaction summaries) are kept for AI context.
                if !db_history.is_empty() {
                    let rebuilt: Vec<ChatMessage> =
                        db_history.iter().map(chat_message_from_entry).collect();
                    // Mutate the live cached object (Python edits `session.history`
                    // on the very object `get_session` returned).
                    s.sessions.with_session_mut(&session_id, |sess| {
                        sess.history = rebuilt;
                    });
                }
                // `history_dict = [m for m in db_history if not (m.get("metadata") or
                // {}).get("hidden")]` — the RESPONSE excludes hidden messages, matching
                // the in-memory path.
                history_dict = db_history
                    .into_iter()
                    .filter(|m| {
                        !m.get("metadata")
                            .and_then(|md| md.get("hidden"))
                            .map(json_is_truthy)
                            .unwrap_or(false)
                    })
                    .collect();
            }
            // `except Exception as e: logger.error(f"DB fallback failed ...")` — the
            // fallback is best-effort; an error leaves `history_dict` empty.
            Err(e) => {
                crate::pylog::error(&format!("DB fallback failed for {session_id}: {e}"));
            }
        }
    }

    // `return {"history": ..., "model": ..., "endpoint_url": ..., "name": ...}`.
    Ok(Json(json!({
        "history": history_dict,
        "model": session.model,
        "endpoint_url": session.endpoint_url,
        "name": session.name,
    }))
    .into_response())
}

// ---------------------------------------------------------------------------
// POST /api/session/{session_id}/truncate
// ---------------------------------------------------------------------------

/// `POST /api/session/{session_id}/truncate` — keep the first `keep_count`
/// messages, drop the rest.
///
/// ```python
/// _verify_session_owner(request, session_id)
/// try:
///     body = await request.json(); keep_count = body.get("keep_count", 0)
///     result = session_manager.truncate_messages(session_id, keep_count)
///     return {"status": "ok", "kept": keep_count, "truncated": result}
/// except KeyError: raise HTTPException(404, "Session not found")
/// except Exception as e: raise HTTPException(500, str(e))
/// ```
async fn truncate_session(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(session_id): Path<String>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    verify_session_owner(&s, user.as_deref(), &session_id)?;

    // `body = await request.json()` — a malformed body raises inside the `try`, so
    // it lands in `except Exception -> 500` (NOT a 422). `keep_count = body.get(
    // "keep_count", 0)`; a non-int json value would later make `truncate_messages`
    // raise -> 500, mirrored by parsing the integer leniently and 500-ing otherwise.
    let body = match parse_json_object(&raw_body) {
        Ok(b) => b,
        Err(e) => return Err(HttpException::new(500, e)),
    };
    let keep_count = match body.get("keep_count") {
        None => 0_i64,
        Some(v) => match json_as_i64(v) {
            Some(n) => n,
            // A non-numeric keep_count would make Python's slice / comparison raise,
            // caught by the broad `except Exception -> 500`.
            None => {
                return Err(HttpException::new(
                    500,
                    "'str' object cannot be interpreted as an integer",
                ))
            }
        },
    };

    // `result = session_manager.truncate_messages(session_id, keep_count)` — may
    // KeyError (missing session) -> 404.
    match s.sessions.truncate_messages(&session_id, keep_count) {
        Ok(result) => Ok(Json(json!({
            "status": "ok",
            "kept": keep_count,
            "truncated": result,
        }))
        .into_response()),
        Err(crate::error::PyError::Key(_)) => Err(HttpException::new(404, "Session not found")),
        Err(e) => {
            crate::pylog::error(&format!("Truncate error {session_id}: {e}"));
            Err(HttpException::new(500, py_err_str(&e)))
        }
    }
}

// ---------------------------------------------------------------------------
// POST /api/session/{session_id}/message
// ---------------------------------------------------------------------------

/// `POST /api/session/{session_id}/message` — add a message to a session (slash
/// command persistence).
///
/// ```python
/// _verify_session_owner(request, session_id)
/// try:
///     body = await request.json()
///     role = body.get("role", "assistant"); content = body.get("content", "")
///     if not content: raise HTTPException(400, "content is required")
///     msg = ChatMessage(role=role, content=content, metadata=body.get("metadata"))
///     session_manager.add_message(session_id, msg)
///     return {"status": "ok"}
/// except KeyError: raise HTTPException(404, "Session not found")
/// ```
async fn add_message(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(session_id): Path<String>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    verify_session_owner(&s, user.as_deref(), &session_id)?;

    // `body = await request.json()`. NOTE: this handler's only `except` is
    // `KeyError -> 404`; a JSON-decode error is NOT caught (no `except Exception`),
    // so FastAPI surfaces it as the framework default. We map a malformed body to
    // the same default-handler 500 the unhandled exception would produce.
    let body = parse_json_object(&raw_body).map_err(|_| HttpException::new(500, "Internal Server Error"))?;

    // `role = body.get("role", "assistant")`
    let role = body
        .get("role")
        .and_then(Value::as_str)
        .unwrap_or("assistant")
        .to_string();
    // `content = body.get("content", "")`
    let content = body
        .get("content")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    // `if not content: raise HTTPException(400, "content is required")`.
    if content.is_empty() {
        return Err(HttpException::new(400, "content is required"));
    }
    // `metadata=body.get("metadata")` — the raw value (object/None); a non-object
    // is passed through to ChatMessage as-is (Python stores whatever it gets).
    let metadata = body.get("metadata").and_then(|v| match v {
        Value::Object(m) => Some(m.clone()),
        Value::Null => None,
        _ => None,
    });
    let msg = ChatMessage::new(role, content, metadata);

    // `session_manager.add_message(session_id, msg)` — may KeyError -> 404. The Rust
    // `add_message` returns `PyResult<()>`; a missing session raises KeyError there.
    match s.sessions.add_message(&session_id, msg) {
        Ok(()) => Ok(Json(json!({ "status": "ok" })).into_response()),
        // `add_message` only raises KeyError in Python; any error here -> 404.
        Err(_) => Err(HttpException::new(404, "Session not found")),
    }
}

// ---------------------------------------------------------------------------
// POST /api/session/{session_id}/delete-messages
// ---------------------------------------------------------------------------

/// `POST /api/session/{session_id}/delete-messages` — delete messages by DB id
/// (or legacy index).
///
/// ```python
/// _verify_session_owner(request, session_id)
/// try:
///     body = await request.json()
///     msg_ids = body.get("msg_ids", []); indices = body.get("indices")
///     session = session_manager.get_session(session_id)
///     db = SessionLocal()
///     if msg_ids: ...delete by id, prune in-memory by _db_id...
///     elif indices: ...legacy index delete (sorted desc) from DB + in-memory...
///     else: return {"status": "ok", "deleted": 0}
///     session.message_count = len(session.history)
///     ...update DbSession.message_count + updated_at...
///     db.commit(); return {"status": "ok", "deleted": deleted}
/// except KeyError: raise HTTPException(404, "Session not found")
/// except Exception as e: raise HTTPException(500, str(e))
/// ```
async fn delete_messages(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(session_id): Path<String>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    verify_session_owner(&s, user.as_deref(), &session_id)?;

    // `body = await request.json()` — malformed -> broad except -> 500.
    let body = match parse_json_object(&raw_body) {
        Ok(b) => b,
        Err(e) => return Err(HttpException::new(500, e)),
    };
    // `msg_ids = body.get("msg_ids", [])`
    let msg_ids: Vec<String> = match body.get("msg_ids") {
        Some(Value::Array(a)) => a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect(),
        _ => Vec::new(),
    };
    // `indices = body.get("indices")` — present-or-None (legacy fallback).
    let indices: Option<Vec<i64>> = match body.get("indices") {
        Some(Value::Array(a)) => Some(a.iter().filter_map(json_as_i64).collect()),
        _ => None,
    };

    // `session = session_manager.get_session(session_id)` — KeyError -> 404.
    if s.sessions.get_session(&session_id).is_err() {
        return Err(HttpException::new(404, "Session not found"));
    }

    // The whole Python mutation sequence runs inside ONE `db = SessionLocal()` and
    // commits exactly once at the very end (`db.commit()`), with an implicit
    // rollback on any exception. Mirror that atomicity with a single rusqlite
    // transaction: every per-id DELETE / legacy-index DELETE and the
    // `message_count`/`db_session` UPDATE run on `tx`, and we `tx.commit()` only
    // after the count is finalized. Any early `?` return drops `tx` un-committed
    // -> rollback, so a mid-op failure persists NOTHING (no partial deletes). The
    // in-memory cache prune is part of the same all-or-nothing flow: Python
    // interleaves it before the commit, exactly as below.
    let result = (|| -> Result<i64, HttpException> {
        let mut conn = crate::core::database::session_local().map_err(db_500)?;
        let tx = conn.transaction().map_err(db_500)?;

        // Python truthiness of `msg_ids` (non-empty list) selects the new path.
        let deleted = if !msg_ids.is_empty() {
            // New ID-based delete: delete each `(id, session_id)` match, counting hits.
            let deleted = db_delete_by_ids(&tx, &session_id, &msg_ids)?;
            // `session.history = [m for m in session.history if _get_db_id(m) not in msg_ids]`
            // — prune the live cache by each message's metadata `_db_id`.
            s.sessions.with_session_mut(&session_id, |sess| {
                sess.history.retain(|m| match get_db_id(m) {
                    Some(id) => !msg_ids.iter().any(|mid| mid == id),
                    None => true,
                });
            });
            finalize_count(&tx, &s, &session_id)?;
            deleted
        } else if indices.as_ref().map(|v| !v.is_empty()).unwrap_or(false) {
            // `elif indices:` — legacy index-based delete (`indices` truthy).
            let indices = indices.unwrap();
            let deleted = db_delete_by_indices(&tx, &s, &session_id, &indices)?;
            finalize_count(&tx, &s, &session_id)?;
            deleted
        } else {
            // `else: return {"status": "ok", "deleted": 0}` — returns BEFORE the
            // count update + commit, so the transaction is dropped (no DB write).
            return Ok(0);
        };

        // `db.commit()` — the SINGLE commit holding every DELETE + the
        // `message_count`/`db_session` UPDATE. Reached only on the two delete paths.
        tx.commit().map_err(db_500)?;
        Ok(deleted)
    })();

    match result {
        Ok(deleted) => Ok(Json(json!({ "status": "ok", "deleted": deleted })).into_response()),
        Err(e) => {
            crate::pylog::error(&format!("Delete messages error {session_id}: {}", e.detail));
            // The broad `except Exception as e -> 500` already carries the raised
            // detail; pass it through unchanged (a 404 from the helpers would have
            // short-circuited above, never reaching here).
            Err(e)
        }
    }
}

// ---------------------------------------------------------------------------
// POST /api/session/{session_id}/edit-message
// ---------------------------------------------------------------------------

/// `POST /api/session/{session_id}/edit-message` — edit one message's content by
/// its DB id (marks it `edited`).
///
/// ```python
/// _verify_session_owner(request, session_id)
/// try:
///     body = await request.json()
///     msg_id = body.get("msg_id"); content = body.get("content")
///     if not msg_id or content is None: raise HTTPException(400, "msg_id and content are required")
///     session = session_manager.get_session(session_id)
///     db_msg = ...filter(id==msg_id, session_id==session_id).first()
///     if not db_msg: raise HTTPException(404, "Message not found")
///     db_msg.content = content; meta['edited'] = True; db_msg.meta_data = json.dumps(meta)
///     ...update in-memory history by _db_id... ; db.commit(); return {"status": "ok"}
/// except KeyError: raise HTTPException(404, "Session not found")
/// except HTTPException: raise
/// except Exception as e: raise HTTPException(500, str(e))
/// ```
async fn edit_message(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(session_id): Path<String>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    verify_session_owner(&s, user.as_deref(), &session_id)?;

    // `body = await request.json()` — malformed -> broad except -> 500.
    let body = match parse_json_object(&raw_body) {
        Ok(b) => b,
        Err(e) => return Err(HttpException::new(500, e)),
    };
    // `msg_id = body.get("msg_id")` / `content = body.get("content")`.
    let msg_id = body.get("msg_id").and_then(Value::as_str).map(str::to_string);
    let content_val = body.get("content").cloned();
    // `if not msg_id or content is None: raise HTTPException(400, ...)`. `not msg_id`
    // is Python truthiness (None / empty string -> 400); `content is None` is strict.
    let msg_id = match msg_id.filter(|m| !m.is_empty()) {
        Some(m) => m,
        None => return Err(HttpException::new(400, "msg_id and content are required")),
    };
    let content = match content_val {
        Some(Value::Null) | None => {
            return Err(HttpException::new(400, "msg_id and content are required"))
        }
        Some(v) => v,
    };
    // `db_msg.content = content` — SQLAlchemy stores the raw value; a non-string
    // content is whatever JSON held. The string form is what the column expects;
    // we bind it as a string (Python would store the object's str repr only on
    // flush type-coercion, but content is `Text`, so a non-str raises on commit ->
    // 500). Reproduce: require a string, else 500 at the would-be commit.
    let content_str = match &content {
        Value::String(sv) => sv.clone(),
        _ => {
            // A non-string content makes the `Text` column bind fail at db.commit()
            // -> caught by `except Exception -> 500`.
            return Err(HttpException::new(500, "Internal Server Error"));
        }
    };

    // `session = session_manager.get_session(session_id)` — KeyError -> 404.
    if s.sessions.get_session(&session_id).is_err() {
        return Err(HttpException::new(404, "Session not found"));
    }

    // `db_msg = ...filter(id==msg_id, session_id==session_id).first()`.
    let existed = match db_edit_message(&session_id, &msg_id, &content_str) {
        Ok(found) => found,
        Err(e) => {
            crate::pylog::error(&format!("Edit message error {session_id}: {e}"));
            return Err(HttpException::new(500, e));
        }
    };
    // `if not db_msg: raise HTTPException(404, "Message not found")` (caught by the
    // `except HTTPException: raise`, so it surfaces as a real 404).
    if !existed {
        return Err(HttpException::new(404, "Message not found"));
    }

    // Update in-memory history: the message whose metadata `_db_id == msg_id` gets
    // `content` + `metadata['edited'] = True`. Only the FIRST match (Python `break`).
    s.sessions.with_session_mut(&session_id, |sess| {
        for hmsg in sess.history.iter_mut() {
            if get_db_id(hmsg) == Some(msg_id.as_str()) {
                hmsg.content = content_str.clone();
                hmsg.metadata
                    .get_or_insert_with(Map::new)
                    .insert("edited".to_string(), Value::Bool(true));
                break;
            }
        }
    });

    Ok(Json(json!({ "status": "ok" })).into_response())
}

// ---------------------------------------------------------------------------
// POST /api/session/{session_id}/mark-stopped
// ---------------------------------------------------------------------------

/// `POST /api/session/{session_id}/mark-stopped` — mark the last assistant message
/// as user-stopped.
///
/// After the in-memory mutation the DB block loads the LAST assistant row
/// (`order_by(timestamp.desc()).first()`), sets `stopped=True`, backfills `model`
/// when absent, and rewrites its `metadata`. (KeyError from `get_session` 404s
/// first.)
///
/// ```python
/// _verify_session_owner(request, session_id)
/// try:
///     session = session_manager.get_session(session_id)
///     for msg in reversed(session.history): ...mark last assistant stopped (+model)...
///     db = SessionLocal()
///     db_messages = db.query(...).filter(role=='assistant').order_by(timestamp.desc()).first()
///     if db_messages:
///         meta = json.loads(db_messages.meta_data) or {}; meta['stopped'] = True
///         if not meta.get('model'): meta['model'] = session.model
///         db_messages.meta_data = json.dumps(meta); db.commit()
///     session_manager.save_sessions(); return {"status": "ok"}
/// except KeyError: raise HTTPException(404, "Session not found")
/// except Exception as e: raise HTTPException(500, str(e))
/// ```
async fn mark_stopped(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(session_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    verify_session_owner(&s, user.as_deref(), &session_id)?;

    // `session = session_manager.get_session(session_id)` — KeyError -> 404.
    let session = match s.sessions.get_session(&session_id) {
        Ok(sess) => sess,
        Err(_) => return Err(HttpException::new(404, "Session not found")),
    };
    let session_model = session.model.clone();

    // In-memory: find the last assistant message; set `stopped=True`, and `model`
    // if missing. (Mutates the live cached object, exactly as Python does.)
    s.sessions.with_session_mut(&session_id, |sess| {
        for msg in sess.history.iter_mut().rev() {
            if msg.role == "assistant" {
                let md = msg.metadata.get_or_insert_with(Map::new);
                md.insert("stopped".to_string(), Value::Bool(true));
                // `if not msg.metadata.get('model'): msg.metadata['model'] = session.model`
                // — Python truthiness: absent / null / empty model gets backfilled.
                if !md.get("model").map(json_is_truthy).unwrap_or(false) {
                    md.insert("model".to_string(), Value::String(session_model.clone()));
                }
                break;
            }
        }
    });

    // DB block: load the last assistant row, set `stopped`/`model`, rewrite meta.
    if let Err(e) = db_mark_stopped(&session_id, &session_model) {
        crate::pylog::error(&format!("Mark stopped error {session_id}: {e}"));
        return Err(HttpException::new(500, e));
    }

    // `session_manager.save_sessions()` — a no-op in the Rust manager.
    s.sessions.save_sessions();
    Ok(Json(json!({ "status": "ok" })).into_response())
}

// ---------------------------------------------------------------------------
// POST /api/session/{session_id}/update-last-meta
// ---------------------------------------------------------------------------

/// `POST /api/session/{session_id}/update-last-meta` — merge metadata into the last
/// assistant message.
///
/// After the in-memory merge the DB block loads the LAST assistant row
/// (`order_by(timestamp.desc()).first()`), merges `meta_update` into its metadata,
/// and rewrites it. (KeyError from `get_session` 404s first.)
///
/// ```python
/// _verify_session_owner(request, session_id)
/// try:
///     body = await request.json(); meta_update = body.get("metadata", {})
///     session = session_manager.get_session(session_id)
///     for msg in reversed(session.history): ...metadata.update(meta_update) on last assistant...
///     db = SessionLocal()
///     db_msg = db.query(...).filter(role=='assistant').order_by(timestamp.desc()).first()
///     if db_msg:
///         meta = json.loads(db_msg.meta_data) or {}; meta.update(meta_update)
///         db_msg.meta_data = json.dumps(meta); db.commit()
///     session_manager.save_sessions(); return {"status": "ok"}
/// except KeyError: raise HTTPException(404, "Session not found")
/// except Exception as e: raise HTTPException(500, str(e))
/// ```
async fn update_last_meta(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(session_id): Path<String>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    verify_session_owner(&s, user.as_deref(), &session_id)?;

    // `body = await request.json()` — malformed -> broad except -> 500.
    let body = match parse_json_object(&raw_body) {
        Ok(b) => b,
        Err(e) => return Err(HttpException::new(500, e)),
    };
    // `meta_update = body.get("metadata", {})`. A non-object metadata would make
    // `metadata.update(meta_update)` raise -> 500; reproduce by requiring an object.
    let meta_update: Map<String, Value> = match body.get("metadata") {
        None => Map::new(),
        Some(Value::Object(m)) => m.clone(),
        Some(_) => {
            // `dict.update(non_mapping)` raises TypeError -> caught -> 500. But the
            // in-memory loop runs first and would hit the same TypeError; Python's
            // order makes the loop the raise site. Either way a 500 results.
            return Err(HttpException::new(
                500,
                "'NoneType' object is not iterable",
            ));
        }
    };

    // `session = session_manager.get_session(session_id)` — KeyError -> 404.
    if s.sessions.get_session(&session_id).is_err() {
        return Err(HttpException::new(404, "Session not found"));
    }

    // In-memory: `metadata.update(meta_update)` on the last assistant message.
    s.sessions.with_session_mut(&session_id, |sess| {
        for msg in sess.history.iter_mut().rev() {
            if msg.role == "assistant" {
                let md = msg.metadata.get_or_insert_with(Map::new);
                for (k, v) in &meta_update {
                    md.insert(k.clone(), v.clone());
                }
                break;
            }
        }
    });

    // DB block: load the last assistant row, merge `meta_update`, rewrite meta.
    if let Err(e) = db_update_last_meta(&session_id, &meta_update) {
        crate::pylog::error(&format!("Update last meta error {session_id}: {e}"));
        return Err(HttpException::new(500, e));
    }

    // `session_manager.save_sessions()` — a no-op in the Rust manager.
    s.sessions.save_sessions();
    Ok(Json(json!({ "status": "ok" })).into_response())
}

// ---------------------------------------------------------------------------
// POST /api/session/{session_id}/merge-last-assistant
// ---------------------------------------------------------------------------

/// `POST /api/session/{session_id}/merge-last-assistant` — merge the last two
/// assistant messages into one (used by "continue").
///
/// The in-memory merge runs fully first. If there are fewer than two assistant
/// messages, Python returns `{"status": "ok", "merged": False}` (200) BEFORE the DB
/// block. Otherwise it reaches the DB block, which loads the session's rows ordered
/// by `timestamp`, rewrites the second-to-last assistant row (`db1`) with the merged
/// content/metadata, deletes everything from the last assistant row (`db2`) down to
/// but not including `db1` (removing the hidden "continue" user message between
/// them), and returns `{"status": "ok", "merged": True}`.
///
/// ```python
/// _verify_session_owner(request, session_id)
/// try:
///     body = await request.json(); separator = body.get("separator", "\n\n")
///     session = session_manager.get_session(session_id)
///     ai_indices = [i for i,msg in enumerate(history) if role=='assistant']
///     if len(ai_indices) < 2: return {"status": "ok", "merged": False}
///     ...merge last two (+ remove the hidden "continue" user msg between)...
///     db = SessionLocal(); db_messages = db.query(...).order_by(timestamp).all()
///     ai_db = [(i,m) for i,m in enumerate(db_messages) if m.role=='assistant']
///     if len(ai_db) >= 2:
///         (_,db1),(_,db2) = ai_db[-2], ai_db[-1]
///         db1.content = merged_content; db1.meta_data = json.dumps(merged_meta)
///         for di in range(idx2, idx1, -1): db.delete(db_messages[di])
///         db.commit()
///     session_manager.save_sessions(); return {"status": "ok", "merged": True}
/// except KeyError: raise HTTPException(404, "Session not found")
/// except Exception as e: raise HTTPException(500, str(e))
/// ```
async fn merge_last_assistant(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(session_id): Path<String>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    verify_session_owner(&s, user.as_deref(), &session_id)?;

    // `body = await request.json()` — malformed -> broad except -> 500.
    let body = match parse_json_object(&raw_body) {
        Ok(b) => b,
        Err(e) => return Err(HttpException::new(500, e)),
    };
    // `separator = body.get("separator", "\n\n")`.
    let separator = body
        .get("separator")
        .and_then(Value::as_str)
        .unwrap_or("\n\n")
        .to_string();

    // `session = session_manager.get_session(session_id)` — KeyError -> 404.
    if s.sessions.get_session(&session_id).is_err() {
        return Err(HttpException::new(404, "Session not found"));
    }

    // In-memory merge of the last two assistant messages. The closure returns the
    // merged content + metadata (so the DB write reuses the SAME values Python
    // computed in-memory), or `None` when fewer than two assistant messages.
    let merged = s
        .sessions
        .with_session_mut(&session_id, |sess| merge_in_memory(sess, &separator))
        // `with_session_mut` returns None only when the session is absent; but we
        // already proved it loads via `get_session` above, so it is present.
        .flatten();

    // `if len(ai_indices) < 2: return {"status": "ok", "merged": False}` — the only
    // 200 path that does NOT touch the DB; it returns BEFORE the DB block.
    let (merged_content, merged_meta) = match merged {
        Some(pair) => pair,
        None => return Ok(Json(json!({ "status": "ok", "merged": false })).into_response()),
    };

    // DB block: rewrite the second-to-last assistant row + delete everything down to
    // (but not including) it. Reuses the in-memory merged content/metadata.
    if let Err(e) = db_merge_last_assistant(&session_id, &merged_content, &merged_meta) {
        crate::pylog::error(&format!("Merge assistant error {session_id}: {e}"));
        return Err(HttpException::new(500, e));
    }

    // `session_manager.save_sessions()` — a no-op in the Rust manager.
    s.sessions.save_sessions();
    Ok(Json(json!({ "status": "ok", "merged": true })).into_response())
}

// ---------------------------------------------------------------------------
// POST /api/session/{session_id}/fork
// ---------------------------------------------------------------------------

/// `POST /api/session/{session_id}/fork` — create a new session with the first
/// `keep_count` messages copied.
///
/// ```python
/// _verify_session_owner(request, session_id)
/// try:
///     body = await request.json(); keep_count = body.get("keep_count", 0)
///     source = session_manager.sessions.get(session_id)
///     if not source: raise HTTPException(404, "Session not found")
///     new_id = str(uuid.uuid4()); fork_name = f"⫝ {source.name}"
///     new_session = session_manager.create_session(new_id, fork_name, source.endpoint_url,
///         source.model, rag=False, owner=getattr(source, 'owner', None))
///     for msg in source.history[:keep_count]: new_session.add_message(ChatMessage(...))
///     try: fire_event("session_created", source.owner) except: ...
///     return {"status": "ok", "id": new_id, "name": fork_name, "kept": len(msgs_to_copy)}
/// except HTTPException: raise
/// except Exception as e: raise HTTPException(500, str(e))
/// ```
async fn fork_session(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(session_id): Path<String>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    verify_session_owner(&s, user.as_deref(), &session_id)?;

    // `body = await request.json()` — malformed -> broad except -> 500.
    let body = match parse_json_object(&raw_body) {
        Ok(b) => b,
        Err(e) => return Err(HttpException::new(500, e)),
    };
    // `keep_count = body.get("keep_count", 0)`.
    let keep_count = match body.get("keep_count") {
        None => 0_i64,
        Some(v) => match json_as_i64(v) {
            Some(n) => n,
            None => return Err(HttpException::new(500, "slice indices must be integers")),
        },
    };

    // `source = session_manager.sessions.get(session_id)` — the IN-MEMORY cache only
    // (no DB hydration), via `peek_session`. `if not source: raise 404`.
    let source = match s.sessions.peek_session(&session_id) {
        Some(src) => src,
        None => return Err(HttpException::new(404, "Session not found")),
    };

    // `new_id = str(uuid.uuid4())`
    let new_id = uuid::Uuid::new_v4().to_string();
    // `fork_name = f"⫝ {source.name}"` — U+2ADD (⫝) then a space then the name.
    let fork_name = format!("\u{2ADD} {}", source.name);

    // `new_session = session_manager.create_session(...)` — rag=False, owner=source.owner.
    let create = s.sessions.create_session(
        &new_id,
        &fork_name,
        &source.endpoint_url,
        &source.model,
        false,
        source.owner.as_deref(),
    );
    if let Err(e) = create {
        crate::pylog::error(&format!("Fork error {session_id}: {e}"));
        return Err(HttpException::new(500, py_err_str(&e)));
    }

    // `msgs_to_copy = source.history[:keep_count]` — Python slice semantics: a
    // negative keep_count slices from the end; clamp to the [0, len] window.
    let msgs_to_copy = slice_prefix(&source.history, keep_count);
    let kept = msgs_to_copy.len();
    // `for msg in msgs_to_copy: new_session.add_message(ChatMessage(msg.role, msg.content, msg.metadata))`.
    // Persisting goes through the manager (the Rust `Session.add_message` clone is a
    // local copy); use `add_message` so the new session's rows hit the DB + cache.
    for msg in msgs_to_copy {
        let _ = s.sessions.add_message(
            &new_id,
            ChatMessage::new(msg.role.clone(), msg.content.clone(), msg.metadata.clone()),
        );
    }

    // `try: fire_event("session_created", source.owner) except Exception: logger.debug(...)`.
    crate::src::event_bus::fire_event("session_created", source.owner.as_deref());

    // `return {"status": "ok", "id": new_id, "name": fork_name, "kept": len(msgs_to_copy)}`.
    Ok(Json(json!({
        "status": "ok",
        "id": new_id,
        "name": fork_name,
        "kept": kept,
    }))
    .into_response())
}

// ---------------------------------------------------------------------------
// GET /api/conversations/topics
// ---------------------------------------------------------------------------

/// `GET /api/conversations/topics` — topic-frequency analysis over the caller's
/// non-archived sessions.
///
/// ```python
/// from src.auth_helpers import require_user
/// user = require_user(request)
/// try: return analyze_topics(session_manager, owner=user or None)
/// except Exception as e: raise HTTPException(500, f"Topic analysis failed: {e}")
/// ```
///
/// `require_user` is the auth gate: an unauthenticated caller (auth configured, not
/// loopback-bypassed) raises **401** `"Not authenticated"`; the single-user /
/// `AUTH_ENABLED=false` / loopback cases return `""`, which `owner=user or None`
/// then maps to `None` (no owner filter). Following the wave-router convention, the
/// client host is `None` here (no `ConnectInfo`), keeping `require_user` strict for
/// the non-loopback case. The already-ported
/// [`crate::src::topic_analyzer::analyze_topics`] consumes a duck-typed
/// `session_manager` exposing `.sessions` as a `{id: session_data}` mapping; we
/// build that snapshot from the live cache via [`SessionsSnapshot`].
async fn get_conversation_topics(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    // `user = require_user(request)` — 401 when unauthenticated (auth configured,
    // not bypassed); `""` in single-user / auth-disabled / loopback modes.
    let user = crate::routes::auth_adapter::require_user(user.as_deref(), &s, None)?;
    // `owner=user or None` — the empty-string single-user case becomes `None` (no
    // owner filter); a real username filters to that owner.
    let owner = if user.is_empty() { None } else { Some(user) };
    // Build the duck-typed `.sessions` mapping `analyze_topics` reads.
    let snapshot = SessionsSnapshot::from_manager(&s.sessions);
    // `return analyze_topics(session_manager, owner=owner)`. `analyze_topics` is
    // pure and infallible here, so the Python `except -> 500` is unreachable.
    let result = crate::src::topic_analyzer::analyze_topics(&snapshot, owner.as_deref());
    Ok(Json(result).into_response())
}

// ---------------------------------------------------------------------------
// POST /api/session/{session_id}/compact  (OWNED here — last-include-wins)
// ---------------------------------------------------------------------------

/// `POST /api/session/{session_id}/compact` — manually compact a session's context.
///
/// Keeps the last 4 messages, summarizes the rest with the utility model, replaces
/// the history with `[hidden system summary, visible assistant summary, ...recent]`,
/// and rewrites the DB rows.
///
/// ```python
/// _verify_session_owner(request, session_id)
/// try: session = session_manager.get_session(session_id)
/// except KeyError: raise HTTPException(404, "Session not found")
/// try:
///     if len(session.history) < 6: return {"status": "ok", "message": "Not enough messages to compact"}
///     ctx_len = get_context_length(...); used_before = estimate_tokens(get_context_messages())
///     keep_count = 4; older = history[:-4]; recent = history[-4:]
///     convo_text = "\n".join(f"{ROLE}: {content[:2000]}" for m in older)
///     util_url, util_model, util_headers = resolve_endpoint("utility")
///     compact_url/model/headers = util_* or session.*
///     compaction_count = sum(1 for m in history if "[Conversation summary" in (m.content or ""))
///     sys_prompt = SELF_SUMMARY_SYSTEM_PROMPT.replace("{count}", len(older)).replace("{n}", compaction_count+1)
///     summary = await llm_call_async(..., temperature=0.2, max_tokens=1024, timeout=30)
///     ...replace session.history; rewrite DB (delete all but last 4, insert 2 summaries, bump session)...
///     return {"status": "ok", "message": ..., "before": pct_before, "after": pct_after}
/// except Exception as e: raise HTTPException(500, str(e))
/// ```
async fn compact_session(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(session_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    verify_session_owner(&s, user.as_deref(), &session_id)?;

    // `try: session = session_manager.get_session(session_id) except KeyError: 404`.
    let session = match s.sessions.get_session(&session_id) {
        Ok(sess) => sess,
        Err(_) => return Err(HttpException::new(404, "Session not found")),
    };

    match compact_inner(&s, &session_id, &session).await {
        Ok(resp) => Ok(resp),
        Err(e) => {
            // The whole compaction body is wrapped in `except Exception as e:
            // raise HTTPException(500, str(e))`. Helper-raised 500s carry their
            // detail; pass through.
            crate::pylog::error(&format!("Manual compact error {session_id}: {}", e.detail));
            Err(e)
        }
    }
}

/// The body of `compact_session` (everything inside the second `try`). Split out so
/// the single `except Exception -> 500` wrapper in the caller reads cleanly.
async fn compact_inner(
    s: &AppState,
    session_id: &str,
    session: &Session,
) -> Result<Response, HttpException> {
    use crate::src::model_context::{estimate_tokens, get_context_length};

    // `if len(session.history) < 6: return {"status": "ok", "message": "Not enough ..."}`.
    if session.history.len() < 6 {
        return Ok(Json(json!({
            "status": "ok",
            "message": "Not enough messages to compact",
        }))
        .into_response());
    }

    // `ctx_len = get_context_length(session.endpoint_url, session.model)`.
    let ctx_len = get_context_length(&session.endpoint_url, &session.model);
    // `messages_before = session.get_context_messages(); used_before = estimate_tokens(...)`.
    let messages_before = session.get_context_messages();
    let used_before = estimate_tokens(&messages_before);
    // `pct_before = round((used_before / ctx_len) * 100, 1) if ctx_len else 0`.
    let pct_before = pct(used_before, ctx_len);
    // `msg_count_before = len(session.history)`.
    let msg_count_before = session.history.len();

    // `keep_count = 4; older = history[:-4]; recent = history[-4:]`.
    let keep_count = 4usize;
    let split = session.history.len() - keep_count;
    let older: Vec<ChatMessage> = session.history[..split].to_vec();
    let recent: Vec<ChatMessage> = session.history[split..].to_vec();

    // `convo_text = "\n".join(f"{ROLE.upper()}: {content[:2000]}" for m in older)`.
    let convo_text: String = older
        .iter()
        .map(|m| {
            let role = m.role.to_uppercase();
            let content: String = m.content.chars().take(2000).collect();
            format!("{role}: {content}")
        })
        .collect::<Vec<_>>()
        .join("\n");

    // `util_url, util_model, util_headers = resolve_endpoint("utility")`. NOTE: this
    // call passes NO fallbacks, so a url can be Some while the model is None — the
    // compact handler then resolves url/model/headers INDEPENDENTLY:
    //   compact_url = util_url or session.endpoint_url
    //   compact_model = util_model or session.model
    //   compact_headers = util_headers if util_url else session.headers
    let (util_url, util_model, util_headers) =
        crate::src::endpoint_resolver::resolve_endpoint("utility", None);
    let compact_url = util_url
        .as_deref()
        .filter(|u| !u.is_empty())
        .unwrap_or(&session.endpoint_url)
        .to_string();
    let compact_model = util_model
        .as_deref()
        .filter(|m| !m.is_empty())
        .unwrap_or(&session.model)
        .to_string();
    // `util_headers if util_url else session.headers` — Python truthiness on
    // `util_url` (the URL, not headers): a truthy URL keeps util_headers.
    let compact_headers = if util_url.as_deref().map(|u| !u.is_empty()).unwrap_or(false) {
        util_headers.unwrap_or_else(|| session.headers.clone())
    } else {
        session.headers.clone()
    };

    // `compaction_count = sum(1 for m in history if "[Conversation summary" in (m.content or ""))`.
    let compaction_count = session
        .history
        .iter()
        .filter(|m| m.content.contains("[Conversation summary"))
        .count();

    // `sys_prompt = SELF_SUMMARY_SYSTEM_PROMPT.replace("{count}", len(older)).replace("{n}", count+1)`.
    let sys_prompt = crate::src::context_compactor::SELF_SUMMARY_SYSTEM_PROMPT
        .replace("{count}", &older.len().to_string())
        .replace("{n}", &(compaction_count + 1).to_string());

    // `summary = await llm_call_async(compact_url, compact_model, [system, user],
    //   temperature=0.2, max_tokens=1024, headers=compact_headers, timeout=30)`.
    let messages = vec![
        json!({ "role": "system", "content": sys_prompt }),
        json!({ "role": "user", "content": convo_text }),
    ];
    let summary = crate::src::llm_core::llm_call_async(
        &compact_url,
        &compact_model,
        messages,
        0.2,
        1024,
        compact_headers,
        30,
    )
    .await
    .map_err(|e| HttpException::new(500, py_err_str(&e)))?;

    // Build the two summary ChatMessages.
    // `system_summary` (hidden, for AI context).
    let system_content =
        format!("[Conversation summary — {} earlier messages were compacted]\n\n{summary}", older.len());
    let mut system_meta = Map::new();
    system_meta.insert("compacted".to_string(), Value::Bool(true));
    system_meta.insert("hidden".to_string(), Value::Bool(true));
    let system_summary = ChatMessage::new("system", system_content.clone(), Some(system_meta.clone()));

    // `summary_msg` (visible assistant, shows stats).
    let summary_content = format!(
        "**Conversation compacted** — {} messages summarized, {} kept.",
        older.len(),
        recent.len()
    );
    let mut summary_meta = Map::new();
    summary_meta.insert("compacted".to_string(), Value::Bool(true));
    summary_meta.insert(
        "messages_removed".to_string(),
        Value::Number((older.len() as i64).into()),
    );
    let summary_msg = ChatMessage::new("assistant", summary_content.clone(), Some(summary_meta.clone()));

    // `new_history = [system_summary, summary_msg] + list(recent)`.
    let mut new_history: Vec<ChatMessage> = Vec::with_capacity(2 + recent.len());
    new_history.push(system_summary);
    new_history.push(summary_msg);
    new_history.extend(recent.iter().cloned());
    let new_len = new_history.len();

    // `session.history = new_history; session.message_count = len(...)` on the live
    // cached object.
    s.sessions.with_session_mut(session_id, |sess| {
        sess.history = new_history;
        sess.message_count = new_len as i64;
    });
    crate::pylog::info(&format!(
        "Compact: session {session_id} history now has {new_len} messages (was {msg_count_before})"
    ));

    // DB rewrite: delete all but the last `keep_count`, insert the two summaries,
    // bump the session row. Raw rusqlite in one transaction.
    db_compact_rewrite(
        session_id,
        keep_count,
        &system_content,
        &serialize_meta(&system_meta),
        &summary_content,
        &serialize_meta(&summary_meta),
        new_len as i64,
    )
    .map_err(|e| HttpException::new(500, e))?;

    // `session_manager.save_sessions()` — a no-op in the Rust manager.
    s.sessions.save_sessions();

    // `used_after = estimate_tokens(session.get_context_messages())` — recompute over
    // the NEW history (read it back from the live cache).
    let after_messages = s
        .sessions
        .peek_session(session_id)
        .map(|sess| sess.get_context_messages())
        .unwrap_or_default();
    let used_after = estimate_tokens(&after_messages);
    let pct_after = pct(used_after, ctx_len);

    // `return {"status": "ok", "message": ..., "before": pct_before, "after": pct_after}`.
    Ok(Json(json!({
        "status": "ok",
        "message": format!(
            "Compacted: {msg_count_before} msgs \u{2192} {new_len} msgs ({pct_before}% \u{2192} {pct_after}%)"
        ),
        "before": pct_before,
        "after": pct_after,
    }))
    .into_response())
}

// ---------------------------------------------------------------------------
// In-memory merge helper (merge_last_assistant)
// ---------------------------------------------------------------------------

/// Merge the last two assistant messages of `sess.history` in place, returning the
/// merged content + metadata when a merge happened (>= 2 assistant messages) and
/// `None` otherwise. The returned pair is the SAME `merged_content` / `merged_meta`
/// the DB write-back reuses (Python computes them once in-memory).
///
/// Ports the Python in-memory block of `merge_last_assistant` exactly: combine the
/// two contents with `separator`, merge metadata (`{**meta1, **meta2}` then drop
/// `stopped`), write into the first message, and remove the second — plus the
/// hidden "continue" user message between them if its content contains "previous
/// response was interrupted".
fn merge_in_memory(sess: &mut Session, separator: &str) -> Option<(String, Map<String, Value>)> {
    // `ai_indices = [i for i,msg in enumerate(history) if role == 'assistant']`.
    let ai_indices: Vec<usize> = sess
        .history
        .iter()
        .enumerate()
        .filter(|(_, m)| m.role == "assistant")
        .map(|(i, _)| i)
        .collect();
    // `if len(ai_indices) < 2: return {"merged": False}`.
    if ai_indices.len() < 2 {
        return None;
    }

    let idx1 = ai_indices[ai_indices.len() - 2];
    let idx2 = ai_indices[ai_indices.len() - 1];

    // `content1 + separator + content2`.
    let content1 = sess.history[idx1].content.clone();
    let content2 = sess.history[idx2].content.clone();
    let merged_content = format!("{content1}{separator}{content2}");

    // `merged_meta = {**meta1, **meta2}; merged_meta.pop('stopped', None)`.
    let meta1 = sess.history[idx1].metadata.clone().unwrap_or_default();
    let meta2 = sess.history[idx2].metadata.clone().unwrap_or_default();
    let mut merged_meta = meta1;
    for (k, v) in meta2 {
        merged_meta.insert(k, v);
    }
    merged_meta.remove("stopped");

    // `msg1.content = merged_content; msg1.metadata = merged_meta`.
    sess.history[idx1].content = merged_content.clone();
    sess.history[idx1].metadata = Some(merged_meta.clone());

    // `remove_indices = [idx2]`; also remove the hidden "continue" user message at
    // `idx2 - 1` when it is a user message containing the interrupt marker.
    let mut remove_indices = vec![idx2];
    if idx2 > 0 && idx2 - 1 > idx1 {
        let between = &sess.history[idx2 - 1];
        if between.role == "user"
            && between.content.contains("previous response was interrupted")
        {
            remove_indices.insert(0, idx2 - 1);
        }
    }
    // `for ri in sorted(remove_indices, reverse=True): session.history.pop(ri)`.
    remove_indices.sort_unstable();
    for ri in remove_indices.into_iter().rev() {
        sess.history.remove(ri);
    }
    Some((merged_content, merged_meta))
}

// ---------------------------------------------------------------------------
// Raw-rusqlite DB helpers (module-private, single-writer)
// ---------------------------------------------------------------------------

/// `db.query(DbChatMessage).filter(session_id == ...).order_by(timestamp).all()` for
/// the `get_history` DB fallback — returns each row as the entry dict
/// `{"role", "content"[, "metadata"]}`, injecting `metadata["timestamp"] =
/// m.timestamp.isoformat() + "Z"` when absent.
fn load_history_from_db(session_id: &str) -> Result<Vec<Value>, String> {
    let conn = crate::core::database::session_local().map_err(|e| e.to_string())?;
    let mut stmt = conn
        .prepare(
            "SELECT role, content, metadata, timestamp FROM chat_messages \
             WHERE session_id = ?1 ORDER BY timestamp",
        )
        .map_err(|e| e.to_string())?;
    let rows = stmt
        .query_map(rusqlite::params![session_id], |r| {
            let role: String = r.get(0)?;
            let content: String = r.get(1)?;
            let meta_data: Option<String> = r.get(2)?;
            let timestamp: Option<String> = r.get(3)?;
            Ok((role, content, meta_data, timestamp))
        })
        .map_err(|e| e.to_string())?;

    let mut out: Vec<Value> = Vec::new();
    for row in rows {
        let (role, content, meta_data, timestamp) = row.map_err(|e| e.to_string())?;
        // `entry = {"role": m.role, "content": m.content}`.
        let mut entry = Map::new();
        entry.insert("role".to_string(), json!(role));
        entry.insert("content".to_string(), json!(content));
        // `meta = json.loads(m.meta_data) or {}` (guarded; bad json -> {}).
        let mut meta: Map<String, Value> = match meta_data.as_deref().filter(|s| !s.is_empty()) {
            Some(s) => match serde_json::from_str::<Value>(s) {
                Ok(Value::Object(m)) => m,
                // `... or {}` — a falsy (null / non-object) parse -> {}.
                _ => Map::new(),
            },
            None => Map::new(),
        };
        // `if m.timestamp and "timestamp" not in meta: meta["timestamp"] = iso + "Z"`.
        if let Some(ts) = timestamp.as_deref().filter(|s| !s.is_empty()) {
            if !meta.contains_key("timestamp") {
                meta.insert(
                    "timestamp".to_string(),
                    json!(format!("{}Z", crate::pydatetime::to_isoformat(ts))),
                );
            }
        }
        // `if meta: entry["metadata"] = meta`.
        if !meta.is_empty() {
            entry.insert("metadata".to_string(), Value::Object(meta));
        }
        out.push(Value::Object(entry));
    }
    Ok(out)
}

/// Delete each `(id, session_id)` matching `chat_messages` row, returning the count
/// deleted (mirrors the per-id `db.delete(db_msg)` loop, `if db_msg: deleted += 1`).
///
/// Runs on the caller's single `tx` so the per-id deletes share one transaction with
/// the trailing `message_count`/`db_session` UPDATE — all-or-nothing, exactly like
/// Python's one `db` + single `db.commit()`. A failed DELETE returns `Err`, dropping
/// `tx` un-committed (rollback) instead of leaving partial autocommitted deletes.
fn db_delete_by_ids(
    tx: &rusqlite::Transaction<'_>,
    session_id: &str,
    msg_ids: &[String],
) -> Result<i64, HttpException> {
    let mut deleted = 0i64;
    for mid in msg_ids {
        // `db_msg = ...filter(id==mid, session_id==session_id).first(); if db_msg: delete`.
        let n = tx
            .execute(
                "DELETE FROM chat_messages WHERE id = ?1 AND session_id = ?2",
                rusqlite::params![mid, session_id],
            )
            .map_err(db_500)?;
        if n > 0 {
            deleted += 1;
        }
    }
    Ok(deleted)
}

/// Legacy index-based delete: load the session's messages ordered by `timestamp`,
/// then delete (and prune in-memory) by each index, processed largest-first.
///
/// ```python
/// indices = sorted(indices, reverse=True)
/// db_messages = query(...).order_by(timestamp).all()
/// for idx in indices:
///     if 0 <= idx < len(db_messages): db.delete(db_messages[idx]); deleted += 1
///     if 0 <= idx < len(session.history): session.history.pop(idx)
/// ```
fn db_delete_by_indices(
    tx: &rusqlite::Transaction<'_>,
    s: &AppState,
    session_id: &str,
    indices: &[i64],
) -> Result<i64, HttpException> {
    // `db_messages = query(...).order_by(timestamp).all()` — load the ids in order.
    let mut stmt = tx
        .prepare("SELECT id FROM chat_messages WHERE session_id = ?1 ORDER BY timestamp")
        .map_err(db_500)?;
    let db_ids: Vec<String> = stmt
        .query_map(rusqlite::params![session_id], |r| r.get::<_, String>(0))
        .map_err(db_500)?
        .collect::<rusqlite::Result<Vec<_>>>()
        .map_err(db_500)?;
    drop(stmt);

    // `indices = sorted(indices, reverse=True)`.
    let mut sorted_indices = indices.to_vec();
    sorted_indices.sort_unstable();
    sorted_indices.reverse();

    let mut deleted = 0i64;
    for idx in sorted_indices {
        // `if 0 <= idx < len(db_messages): db.delete(db_messages[idx]); deleted += 1`.
        if idx >= 0 && (idx as usize) < db_ids.len() {
            let n = tx
                .execute(
                    "DELETE FROM chat_messages WHERE id = ?1",
                    rusqlite::params![db_ids[idx as usize]],
                )
                .map_err(db_500)?;
            // Python counts the delete unconditionally (the row was just loaded).
            let _ = n;
            deleted += 1;
        }
        // `if 0 <= idx < len(session.history): session.history.pop(idx)` — prune the
        // live cache at the same index.
        s.sessions.with_session_mut(session_id, |sess| {
            if idx >= 0 && (idx as usize) < sess.history.len() {
                sess.history.remove(idx as usize);
            }
        });
    }
    Ok(deleted)
}

/// `session.message_count = len(history)` + `if db_session: db_session.message_count
/// = len(history); db_session.updated_at = now`. The in-memory count is updated via
/// `with_session_mut`; the DB row UPDATE runs on the caller's `tx` so it shares the
/// SINGLE transaction with the preceding deletes — the actual `db.commit()` is the
/// caller's `tx.commit()`. Matches the order Python uses `len(session.history)` for
/// both.
fn finalize_count(
    tx: &rusqlite::Transaction<'_>,
    s: &AppState,
    session_id: &str,
) -> Result<(), HttpException> {
    // `session.message_count = len(session.history)` (read the now-pruned cache len).
    let count = s
        .sessions
        .with_session_mut(session_id, |sess| {
            sess.message_count = sess.history.len() as i64;
            sess.message_count
        })
        .unwrap_or(0);
    // `db_session = ...filter(id==session_id).first(); if db_session: ...` — the UPDATE
    // is staged on `tx`; the commit happens once back in the handler.
    let now = crate::pydatetime::utcnow_naive_iso();
    tx.execute(
        "UPDATE sessions SET message_count = ?1, updated_at = ?2 WHERE id = ?3",
        rusqlite::params![count, now, session_id],
    )
    .map_err(db_500)?;
    Ok(())
}

/// `db_msg = ...filter(id==msg_id, session_id==session_id).first()`; when found, set
/// `content`, merge `meta['edited'] = True` into the existing metadata, and commit.
/// Returns `true` when a row existed (and was updated), `false` otherwise.
fn db_edit_message(session_id: &str, msg_id: &str, content: &str) -> Result<bool, String> {
    let conn = crate::core::database::session_local().map_err(|e| e.to_string())?;
    // Load the existing metadata to merge `edited` into it.
    let existing: Option<Option<String>> = conn
        .query_row(
            "SELECT metadata FROM chat_messages WHERE id = ?1 AND session_id = ?2",
            rusqlite::params![msg_id, session_id],
            |r| r.get::<_, Option<String>>(0),
        )
        .map(Some)
        .or_else(|e| match e {
            rusqlite::Error::QueryReturnedNoRows => Ok(None),
            other => Err(other),
        })
        .map_err(|e| e.to_string())?;

    let existing = match existing {
        Some(m) => m,
        // `if not db_msg:` — no row.
        None => return Ok(false),
    };

    // `meta = json.loads(db_msg.meta_data) if db_msg.meta_data else {}` (bad json -> {}
    // via the inner try); `meta['edited'] = True; db_msg.meta_data = json.dumps(meta)`.
    let mut meta: Map<String, Value> = match existing.as_deref().filter(|s| !s.is_empty()) {
        Some(s) => match serde_json::from_str::<Value>(s) {
            Ok(Value::Object(m)) => m,
            // A non-object / parse failure leaves `meta = {}` (the `except: pass`).
            _ => Map::new(),
        },
        None => Map::new(),
    };
    meta.insert("edited".to_string(), Value::Bool(true));
    let meta_json = serde_json::to_string(&Value::Object(meta)).map_err(|e| e.to_string())?;

    conn.execute(
        "UPDATE chat_messages SET content = ?1, metadata = ?2 WHERE id = ?3 AND session_id = ?4",
        rusqlite::params![content, meta_json, msg_id, session_id],
    )
    .map_err(|e| e.to_string())?;
    Ok(true)
}

/// Load the existing metadata of the LAST assistant row of `session_id`, returning
/// `(id, parsed_metadata)`, or `None` when the session has no assistant row.
///
/// `db.query(...).filter(role=='assistant').order_by(timestamp.desc()).first()` then
/// `meta = json.loads(.meta_data) or {}` (bad / non-object json -> `{}`).
#[allow(clippy::type_complexity)] // (content, metadata-map) tuple mirrors the Python return
fn load_last_assistant_meta(
    conn: &rusqlite::Connection,
    session_id: &str,
) -> Result<Option<(String, Map<String, Value>)>, String> {
    let row: Option<(String, Option<String>)> = conn
        .query_row(
            "SELECT id, metadata FROM chat_messages \
             WHERE session_id = ?1 AND role = 'assistant' ORDER BY timestamp DESC LIMIT 1",
            rusqlite::params![session_id],
            |r| Ok((r.get::<_, String>(0)?, r.get::<_, Option<String>>(1)?)),
        )
        .map(Some)
        .or_else(|e| match e {
            rusqlite::Error::QueryReturnedNoRows => Ok(None),
            other => Err(other),
        })
        .map_err(|e| e.to_string())?;

    let (id, raw_meta) = match row {
        Some(r) => r,
        // `if db_msg:` — no assistant row, the whole DB block is skipped.
        None => return Ok(None),
    };
    // `meta = json.loads(.meta_data) or {}` — a falsy (null / non-object / bad) parse
    // collapses to an empty dict.
    let meta: Map<String, Value> = match raw_meta.as_deref().filter(|s| !s.is_empty()) {
        Some(s) => match serde_json::from_str::<Value>(s) {
            Ok(Value::Object(m)) => m,
            _ => Map::new(),
        },
        None => Map::new(),
    };
    Ok(Some((id, meta)))
}

/// `mark_stopped` DB block: load the last assistant row, set `meta['stopped'] = True`,
/// backfill `meta['model']` from `session.model` when absent, rewrite `meta_data`,
/// and commit. A missing assistant row is a no-op (the `if db_messages:` guard).
fn db_mark_stopped(session_id: &str, session_model: &str) -> Result<(), String> {
    let conn = crate::core::database::session_local().map_err(|e| e.to_string())?;
    let (id, mut meta) = match load_last_assistant_meta(&conn, session_id)? {
        Some(found) => found,
        None => return Ok(()),
    };
    // `meta['stopped'] = True`.
    meta.insert("stopped".to_string(), Value::Bool(true));
    // `if not meta.get('model'): meta['model'] = session.model` — Python truthiness:
    // absent / null / empty model gets backfilled.
    if !meta.get("model").map(json_is_truthy).unwrap_or(false) {
        meta.insert("model".to_string(), Value::String(session_model.to_string()));
    }
    let meta_json = serde_json::to_string(&Value::Object(meta)).map_err(|e| e.to_string())?;
    conn.execute(
        "UPDATE chat_messages SET metadata = ?1 WHERE id = ?2",
        rusqlite::params![meta_json, id],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

/// `update_last_meta` DB block: load the last assistant row, `meta.update(meta_update)`,
/// rewrite `meta_data`, and commit. A missing assistant row is a no-op.
fn db_update_last_meta(session_id: &str, meta_update: &Map<String, Value>) -> Result<(), String> {
    let conn = crate::core::database::session_local().map_err(|e| e.to_string())?;
    let (id, mut meta) = match load_last_assistant_meta(&conn, session_id)? {
        Some(found) => found,
        None => return Ok(()),
    };
    // `meta.update(meta_update)`.
    for (k, v) in meta_update {
        meta.insert(k.clone(), v.clone());
    }
    let meta_json = serde_json::to_string(&Value::Object(meta)).map_err(|e| e.to_string())?;
    conn.execute(
        "UPDATE chat_messages SET metadata = ?1 WHERE id = ?2",
        rusqlite::params![meta_json, id],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

/// `merge_last_assistant` DB block (one transaction):
/// ```python
/// db_messages = db.query(...).order_by(timestamp).all()
/// ai_db = [(i,m) for i,m in enumerate(db_messages) if m.role == 'assistant']
/// if len(ai_db) >= 2:
///     (_, db1), (_, db2) = ai_db[-2], ai_db[-1]
///     db1.content = merged_content; db1.meta_data = json.dumps(merged_meta)
///     db_idx2 = db_messages.index(db2); db_idx1 = db_messages.index(db1)
///     for di in range(db_idx2, db_idx1, -1): db.delete(db_messages[di])
///     db.commit()
/// ```
/// Rewrites the second-to-last assistant row with the merged content/metadata, then
/// deletes every row from the last assistant row (`db_idx2`) down to but NOT
/// including the rewritten row (`db_idx1`) — i.e. the last assistant row plus any
/// "continue" user message between them. A session with `< 2` assistant rows is a
/// no-op (the `if len(ai_db) >= 2:` guard).
fn db_merge_last_assistant(
    session_id: &str,
    merged_content: &str,
    merged_meta: &Map<String, Value>,
) -> Result<(), String> {
    let mut conn = crate::core::database::session_local().map_err(|e| e.to_string())?;
    let tx = conn.transaction().map_err(|e| e.to_string())?;

    // `db_messages = db.query(...).order_by(timestamp).all()` — `(id, role)` in order.
    let rows: Vec<(String, String)> = {
        let mut stmt = tx
            .prepare(
                "SELECT id, role FROM chat_messages WHERE session_id = ?1 ORDER BY timestamp",
            )
            .map_err(|e| e.to_string())?;
        let v = stmt
            .query_map(rusqlite::params![session_id], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?))
            })
            .map_err(|e| e.to_string())?
            .collect::<rusqlite::Result<Vec<_>>>()
            .map_err(|e| e.to_string())?;
        v
    };

    // `ai_db = [(i,m) for i,m in enumerate(db_messages) if m.role == 'assistant']`.
    let ai_db: Vec<usize> = rows
        .iter()
        .enumerate()
        .filter(|(_, (_, role))| role == "assistant")
        .map(|(i, _)| i)
        .collect();
    // `if len(ai_db) >= 2:` — otherwise nothing is rewritten/deleted (and no commit).
    if ai_db.len() < 2 {
        return Ok(());
    }

    // `(_, db1), (_, db2) = ai_db[-2], ai_db[-1]` — the row positions in `db_messages`.
    let db_idx1 = ai_db[ai_db.len() - 2];
    let db_idx2 = ai_db[ai_db.len() - 1];

    // `db1.content = merged_content; db1.meta_data = json.dumps(merged_meta)`.
    let meta_json =
        serde_json::to_string(&Value::Object(merged_meta.clone())).map_err(|e| e.to_string())?;
    tx.execute(
        "UPDATE chat_messages SET content = ?1, metadata = ?2 WHERE id = ?3",
        rusqlite::params![merged_content, meta_json, rows[db_idx1].0],
    )
    .map_err(|e| e.to_string())?;

    // `for di in range(db_idx2, db_idx1, -1): db.delete(db_messages[di])` — delete the
    // last assistant row and everything between it and `db1` (the continue user msg).
    let mut di = db_idx2;
    while di > db_idx1 {
        tx.execute(
            "DELETE FROM chat_messages WHERE id = ?1",
            rusqlite::params![rows[di].0],
        )
        .map_err(|e| e.to_string())?;
        di -= 1;
    }

    tx.commit().map_err(|e| e.to_string())?;
    Ok(())
}

/// Compact DB rewrite (one transaction):
/// 1. `db_msgs = query(...).order_by(timestamp).all(); for m in db_msgs[:-keep_count]: db.delete(m)`.
/// 2. insert the hidden system summary + visible assistant summary (`timestamp=now`).
/// 3. `db_session.message_count = new_len; db_session.updated_at = now; db.commit()`.
#[allow(clippy::too_many_arguments)]
fn db_compact_rewrite(
    session_id: &str,
    keep_count: usize,
    system_content: &str,
    system_meta_json: &str,
    summary_content: &str,
    summary_meta_json: &str,
    new_len: i64,
) -> Result<(), String> {
    let mut conn = crate::core::database::session_local().map_err(|e| e.to_string())?;
    let tx = conn.transaction().map_err(|e| e.to_string())?;

    // `db_msgs = ...order_by(timestamp).all()` then delete all but the last keep_count.
    let ids: Vec<String> = {
        let mut stmt = tx
            .prepare("SELECT id FROM chat_messages WHERE session_id = ?1 ORDER BY timestamp")
            .map_err(|e| e.to_string())?;
        let v = stmt
            .query_map(rusqlite::params![session_id], |r| r.get::<_, String>(0))
            .map_err(|e| e.to_string())?
            .collect::<rusqlite::Result<Vec<_>>>()
            .map_err(|e| e.to_string())?;
        v
    };
    // `for m in db_msgs[:-keep_count]: db.delete(m)` — all but the trailing keep_count.
    let cut = ids.len().saturating_sub(keep_count);
    for id in &ids[..cut] {
        tx.execute(
            "DELETE FROM chat_messages WHERE id = ?1",
            rusqlite::params![id],
        )
        .map_err(|e| e.to_string())?;
    }

    // `now = datetime.now(timezone.utc)`; insert the two summaries with that timestamp.
    let now = crate::pydatetime::utcnow_naive_iso();
    let sys_id = uuid::Uuid::new_v4().to_string();
    tx.execute(
        "INSERT INTO chat_messages (id, session_id, role, content, metadata, timestamp) \
         VALUES (?1, ?2, 'system', ?3, ?4, ?5)",
        rusqlite::params![sys_id, session_id, system_content, system_meta_json, now],
    )
    .map_err(|e| e.to_string())?;
    let summary_id = uuid::Uuid::new_v4().to_string();
    tx.execute(
        "INSERT INTO chat_messages (id, session_id, role, content, metadata, timestamp) \
         VALUES (?1, ?2, 'assistant', ?3, ?4, ?5)",
        rusqlite::params![summary_id, session_id, summary_content, summary_meta_json, now],
    )
    .map_err(|e| e.to_string())?;

    // `if db_session: db_session.message_count = len(history); db_session.updated_at = now`.
    tx.execute(
        "UPDATE sessions SET message_count = ?1, updated_at = ?2 WHERE id = ?3",
        rusqlite::params![new_len, now, session_id],
    )
    .map_err(|e| e.to_string())?;

    tx.commit().map_err(|e| e.to_string())?;
    Ok(())
}

// ---------------------------------------------------------------------------
// topic_analyzer adapter
// ---------------------------------------------------------------------------

/// A snapshot of the live session cache as the `{id: session_data}` mapping the
/// duck-typed [`crate::src::topic_analyzer::analyze_topics`] reads.
///
/// `analyze_topics` only touches `session_data.get("archived")`, `.get("owner")`,
/// `.get("name")`, and `.get("history")` (each history msg as a dict with
/// `.get("content")` / `.get("role")`). We materialize each cached `Session` into
/// exactly that dict shape so the ported analyzer runs unchanged.
struct SessionsSnapshot {
    sessions: Map<String, Value>,
}

impl SessionsSnapshot {
    /// Build the snapshot from the manager's in-memory cache. `peek_session` is the
    /// per-id cache read; we enumerate the live ids and project each session. The
    /// `topics` endpoint scans whatever is currently loaded — exactly the live
    /// `session_manager.sessions` dict Python iterates.
    fn from_manager(manager: &crate::core::session_manager::SessionManager) -> Self {
        let mut sessions = Map::new();
        // `get_sessions_for_user(None)` clones the WHOLE in-memory cache — exactly
        // the `session_manager.sessions` dict Python's `analyze_topics` iterates
        // (the owner filter is applied INSIDE `analyze_topics`, not here).
        for (id, sess) in manager.get_sessions_for_user(None) {
            sessions.insert(id, session_to_value(&sess));
        }
        SessionsSnapshot { sessions }
    }
}

impl crate::src::topic_analyzer::SessionManager for SessionsSnapshot {
    fn sessions(&self) -> &Map<String, Value> {
        &self.sessions
    }
}

/// Project a `Session` to the `session_data` dict shape `analyze_topics` reads.
fn session_to_value(sess: &Session) -> Value {
    let history: Vec<Value> = sess
        .history
        .iter()
        .map(|m| {
            json!({
                "role": m.role,
                "content": m.content,
            })
        })
        .collect();
    json!({
        "archived": sess.archived,
        "owner": sess.owner,
        "name": sess.name,
        "history": history,
    })
}

// ---------------------------------------------------------------------------
// Small shared helpers
// ---------------------------------------------------------------------------

/// `user = get_current_user(request)` — the resolved username or `None` (the
/// load-bearing auth-disabled / single-user case).
fn current_user(user: &Option<Extension<CurrentUser>>) -> Option<String> {
    user.as_ref().map(|Extension(CurrentUser(u))| u.clone())
}

/// `_get_db_id(m)` — read `m.metadata['_db_id']` (a string) or `None`. The Rust
/// history is always typed `ChatMessage`, so the Python `isinstance(m, dict)` arm is
/// dead; only the `ChatMessage.metadata` branch survives.
fn get_db_id(m: &ChatMessage) -> Option<&str> {
    m.metadata
        .as_ref()
        .and_then(|md| md.get("_db_id"))
        .and_then(Value::as_str)
}

/// Parse `await request.json()` to an object, returning the would-be Python
/// exception string on failure (so the broad `except Exception -> 500` carries it).
/// FastAPI's `request.json()` raises `json.JSONDecodeError` for malformed input; a
/// non-object top-level value (e.g. a JSON list) would make `body.get(...)` raise
/// `AttributeError` — both land in the same `except` here.
fn parse_json_object(raw: &[u8]) -> Result<Map<String, Value>, String> {
    match serde_json::from_slice::<Value>(raw) {
        Ok(Value::Object(m)) => Ok(m),
        // A non-object top-level value: `body.get(...)` would AttributeError in
        // Python. Surface a generic decode-ish message (the exact text is not
        // asserted by tests; it only flows into a 500 body).
        Ok(_) => Err("'list' object has no attribute 'get'".to_string()),
        Err(_) => Err("Expecting value: line 1 column 1 (char 0)".to_string()),
    }
}

/// Python `bool(value)` over a JSON value: `None`/`False`/`0`/`""`/`[]`/`{}` falsy.
fn json_is_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `int(value)` over a JSON number — accepts integers and integral floats; `None`
/// for non-numeric values (drives the per-handler 500 those would raise in Python).
fn json_as_i64(v: &Value) -> Option<i64> {
    match v {
        Value::Number(n) => n
            .as_i64()
            .or_else(|| n.as_f64().map(|f| f as i64)),
        Value::Bool(b) => Some(*b as i64), // Python `bool` is an `int` subclass.
        _ => None,
    }
}

/// `history[:keep_count]` with Python slice semantics: a non-negative count clamps to
/// `len`; a negative count slices from the end (`len + count`, clamped at 0).
fn slice_prefix(history: &[ChatMessage], keep_count: i64) -> Vec<ChatMessage> {
    let len = history.len() as i64;
    let end = if keep_count < 0 {
        (len + keep_count).max(0)
    } else {
        keep_count.min(len)
    };
    history[..end as usize].to_vec()
}

/// `round((used / ctx_len) * 100, 1) if ctx_len else 0` — Python `round` is
/// banker's rounding (round-half-to-even), which `f64::round` is NOT, so reproduce
/// it for the percentage. Returns a JSON-ready value (the integer `0` literal when
/// `ctx_len == 0`, mirroring Python's `else 0`).
fn pct(used: i64, ctx_len: i64) -> Value {
    if ctx_len == 0 {
        return json!(0);
    }
    let raw = (used as f64 / ctx_len as f64) * 100.0;
    json!(round_half_even(raw, 1))
}

/// Python `round(x, 1)` — round-half-to-even at one decimal place.
fn round_half_even(x: f64, ndigits: i32) -> f64 {
    let factor = 10f64.powi(ndigits);
    let scaled = x * factor;
    let floor = scaled.floor();
    let diff = scaled - floor;
    let rounded = if (diff - 0.5).abs() < f64::EPSILON {
        // Halfway: round to even.
        if (floor as i64) % 2 == 0 {
            floor
        } else {
            floor + 1.0
        }
    } else {
        scaled.round()
    };
    rounded / factor
}

/// `str(e)` for a raised [`crate::error::PyError`] — the message FastAPI would carry
/// into `HTTPException(500, str(e))`.
fn py_err_str(e: &crate::error::PyError) -> String {
    e.to_string()
}

/// `json.dumps(metadata)` — serialize a metadata map to the compact JSON string the
/// `chat_messages.metadata` column stores.
fn serialize_meta(meta: &Map<String, Value>) -> String {
    serde_json::to_string(&Value::Object(meta.clone())).unwrap_or_else(|_| "{}".to_string())
}

/// Build a `ChatMessage` from a `get_history` entry dict (`{"role", "content"[,
/// "metadata"]}`), mirroring `ChatMessage(role=m["role"], content=m["content"],
/// metadata=m.get("metadata"))` in the DB-fallback write-back.
fn chat_message_from_entry(entry: &Value) -> ChatMessage {
    let role = entry.get("role").and_then(Value::as_str).unwrap_or("").to_string();
    let content = entry
        .get("content")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let metadata = match entry.get("metadata") {
        Some(Value::Object(m)) => Some(m.clone()),
        _ => None,
    };
    ChatMessage::new(role, content, metadata)
}

/// Map a `rusqlite::Error` to a 500 (an unhandled handler exception).
fn db_500(e: rusqlite::Error) -> HttpException {
    crate::pylog::error(&format!("history_routes DB error: {e}"));
    HttpException::new(500, e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn msg(role: &str, content: &str, md: Option<Map<String, Value>>) -> ChatMessage {
        ChatMessage::new(role, content, md)
    }

    #[test]
    fn slice_prefix_python_semantics() {
        let h = vec![
            msg("user", "a", None),
            msg("assistant", "b", None),
            msg("user", "c", None),
        ];
        assert_eq!(slice_prefix(&h, 0).len(), 0);
        assert_eq!(slice_prefix(&h, 2).len(), 2);
        // count > len clamps to len.
        assert_eq!(slice_prefix(&h, 10).len(), 3);
        // negative slices from the end: history[:-1] keeps 2.
        assert_eq!(slice_prefix(&h, -1).len(), 2);
        // history[:-10] clamps to 0.
        assert_eq!(slice_prefix(&h, -10).len(), 0);
    }

    #[test]
    fn get_db_id_reads_metadata() {
        let mut md = Map::new();
        md.insert("_db_id".to_string(), json!("abc"));
        let m = msg("assistant", "x", Some(md));
        assert_eq!(get_db_id(&m), Some("abc"));
        assert_eq!(get_db_id(&msg("user", "y", None)), None);
    }

    #[test]
    fn round_half_even_matches_python_round() {
        // Python: round(12.25, 1) == 12.2 (banker's), round(12.35, 1) == 12.3 (binary repr).
        assert_eq!(round_half_even(50.0, 1), 50.0);
        assert_eq!(round_half_even(33.33, 1), 33.3);
        assert_eq!(round_half_even(33.35, 1), 33.4);
        assert_eq!(round_half_even(0.0, 1), 0.0);
        assert_eq!(round_half_even(66.666, 1), 66.7);
    }

    #[test]
    fn pct_zero_ctx_returns_integer_zero() {
        assert_eq!(pct(100, 0), json!(0));
        // 250 / 1000 * 100 = 25.0.
        assert_eq!(pct(250, 1000), json!(25.0));
    }

    #[test]
    fn json_truthiness_matches_python_bool() {
        assert!(!json_is_truthy(&Value::Null));
        assert!(!json_is_truthy(&json!(false)));
        assert!(json_is_truthy(&json!(true)));
        assert!(!json_is_truthy(&json!(0)));
        assert!(json_is_truthy(&json!(1)));
        assert!(!json_is_truthy(&json!("")));
        assert!(json_is_truthy(&json!("x")));
        assert!(!json_is_truthy(&json!([])));
        assert!(!json_is_truthy(&json!({})));
    }

    #[test]
    fn json_as_i64_coercion() {
        assert_eq!(json_as_i64(&json!(5)), Some(5));
        assert_eq!(json_as_i64(&json!(3.0)), Some(3));
        assert_eq!(json_as_i64(&json!(true)), Some(1));
        assert_eq!(json_as_i64(&json!("x")), None);
        assert_eq!(json_as_i64(&json!(null)), None);
    }

    #[test]
    fn parse_json_object_rejects_non_object() {
        assert!(parse_json_object(b"{\"a\":1}").is_ok());
        assert!(parse_json_object(b"[1,2,3]").is_err());
        assert!(parse_json_object(b"not json").is_err());
    }

    #[test]
    fn merge_in_memory_merges_last_two_assistants() {
        let mut sess = Session::default();
        let mut stopped_md = Map::new();
        stopped_md.insert("stopped".to_string(), json!(true));
        stopped_md.insert("model".to_string(), json!("m1"));
        sess.history = vec![
            msg("user", "q1", None),
            msg("assistant", "a1", None),
            msg("user", "previous response was interrupted; continue", None),
            msg("assistant", "a2", Some(stopped_md)),
        ];
        let merged = merge_in_memory(&mut sess, "\n\n");
        // The returned pair is the merged content + metadata reused by the DB write.
        let (mc, mm) = merged.expect("merge happened");
        assert_eq!(mc, "a1\n\na2");
        assert!(!mm.contains_key("stopped"));
        assert_eq!(mm.get("model"), Some(&json!("m1")));
        // The hidden continue user-message was removed; the two assistants merged.
        assert_eq!(sess.history.len(), 2);
        assert_eq!(sess.history[0].role, "user");
        assert_eq!(sess.history[1].role, "assistant");
        assert_eq!(sess.history[1].content, "a1\n\na2");
        // `stopped` was dropped from the merged metadata; `model` survives.
        let md = sess.history[1].metadata.as_ref().unwrap();
        assert!(!md.contains_key("stopped"));
        assert_eq!(md.get("model"), Some(&json!("m1")));
    }

    #[test]
    fn merge_in_memory_under_two_assistants_is_noop() {
        let mut sess = Session {
            history: vec![msg("user", "q", None), msg("assistant", "a", None)],
            ..Default::default()
        };
        assert!(merge_in_memory(&mut sess, "\n\n").is_none());
        assert_eq!(sess.history.len(), 2);
    }

    #[test]
    fn merge_in_memory_keeps_non_interrupt_user_between() {
        let mut sess = Session {
            history: vec![
                msg("assistant", "a1", None),
                msg("user", "a normal follow up", None),
                msg("assistant", "a2", None),
            ],
            ..Default::default()
        };
        let merged = merge_in_memory(&mut sess, " | ");
        let (mc, _) = merged.expect("merge happened");
        assert_eq!(mc, "a1 | a2");
        // The between user-message is NOT the interrupt marker, so it stays.
        assert_eq!(sess.history.len(), 2);
        assert_eq!(sess.history[0].content, "a1 | a2");
        assert_eq!(sess.history[1].role, "user");
    }

    #[test]
    fn chat_message_from_entry_round_trips() {
        let mut md = Map::new();
        md.insert("_db_id".to_string(), json!("id1"));
        let entry = json!({ "role": "assistant", "content": "hi", "metadata": md });
        let m = chat_message_from_entry(&entry);
        assert_eq!(m.role, "assistant");
        assert_eq!(m.content, "hi");
        assert_eq!(get_db_id(&m), Some("id1"));
        // No metadata key -> None.
        let bare = chat_message_from_entry(&json!({ "role": "user", "content": "q" }));
        assert!(bare.metadata.is_none());
    }

    #[test]
    fn session_to_value_shape_for_topics() {
        let sess = Session {
            name: "S".to_string(),
            owner: Some("alice".to_string()),
            archived: true,
            history: vec![msg("user", "python code", None)],
            ..Default::default()
        };
        let v = session_to_value(&sess);
        assert_eq!(v["archived"], json!(true));
        assert_eq!(v["owner"], json!("alice"));
        assert_eq!(v["name"], json!("S"));
        assert_eq!(v["history"][0]["role"], json!("user"));
        assert_eq!(v["history"][0]["content"], json!("python code"));
    }

    // ── DB-backed: mark_stopped / update_last_meta / merge_last_assistant ──
    //
    // Each test points `DATABASE_URL` at a UNIQUE temp file, builds the schema,
    // and seeds `chat_messages` rows. A process-global lock serializes them so the
    // shared env var is never read mid-swap by a sibling DB test. Never the live
    // data dir.
    use crate::core::database::DB_TEST_LOCK;

    struct TmpDb(std::path::PathBuf);
    impl Drop for TmpDb {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }

    fn fresh_temp_db(tag: &str) -> TmpDb {
        let dir = std::env::temp_dir();
        let unique = format!(
            "odysseus_history_routes_{tag}_{}_{}.db",
            std::process::id(),
            uuid::Uuid::new_v4().simple()
        );
        let path = dir.join(unique);
        let _ = std::fs::remove_file(&path);
        std::env::set_var("DATABASE_URL", format!("sqlite:///{}", path.display()));
        crate::core::database::create_all().unwrap();
        TmpDb(path)
    }

    /// Seed the parent `sessions` row. `chat_messages.session_id` is a NOT NULL FK to
    /// `sessions(id)` (PRAGMA foreign_keys=ON), so every message-seeding test must
    /// insert the owning session first or the child INSERT fails with FOREIGN KEY
    /// constraint (code 787).
    fn seed_session(conn: &rusqlite::Connection, id: &str) {
        let ts = "2024-01-01 00:00:00";
        conn.execute(
            "INSERT INTO sessions (id, name, endpoint_url, model, headers, created_at, updated_at) \
             VALUES (?1, 'sess', 'http://x', 'm', '{}', ?2, ?2)",
            rusqlite::params![id, ts],
        )
        .unwrap();
    }

    /// Seed one `chat_messages` row with an explicit string `timestamp` (so lexical
    /// ordering is deterministic) and return its id.
    fn seed_msg(
        conn: &rusqlite::Connection,
        session_id: &str,
        role: &str,
        content: &str,
        metadata: Option<&str>,
        timestamp: &str,
    ) -> String {
        let id = uuid::Uuid::new_v4().to_string();
        conn.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, metadata, timestamp) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            rusqlite::params![id, session_id, role, content, metadata, timestamp],
        )
        .unwrap();
        id
    }

    fn read_meta(conn: &rusqlite::Connection, id: &str) -> Map<String, Value> {
        let raw: Option<String> = conn
            .query_row(
                "SELECT metadata FROM chat_messages WHERE id = ?1",
                rusqlite::params![id],
                |r| r.get(0),
            )
            .unwrap();
        match raw.as_deref().filter(|s| !s.is_empty()) {
            Some(s) => match serde_json::from_str::<Value>(s) {
                Ok(Value::Object(m)) => m,
                _ => Map::new(),
            },
            None => Map::new(),
        }
    }

    #[test]
    fn db_mark_stopped_marks_last_assistant_row() {
        let _g = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db("mark_stopped");
        let conn = crate::core::database::session_local().unwrap();
        let sid = "s1";
        seed_session(&conn, sid);
        seed_msg(&conn, sid, "user", "q1", None, "2024-01-01 00:00:00");
        let a1 = seed_msg(&conn, sid, "assistant", "a1", None, "2024-01-01 00:00:01");
        seed_msg(&conn, sid, "user", "q2", None, "2024-01-01 00:00:02");
        let a2 = seed_msg(&conn, sid, "assistant", "a2", None, "2024-01-01 00:00:03");
        drop(conn);

        db_mark_stopped(sid, "the-model").unwrap();

        let conn = crate::core::database::session_local().unwrap();
        // The LAST assistant row (a2) got stopped=True + model backfilled.
        let m2 = read_meta(&conn, &a2);
        assert_eq!(m2.get("stopped"), Some(&json!(true)));
        assert_eq!(m2.get("model"), Some(&json!("the-model")));
        // The earlier assistant row (a1) is untouched.
        assert!(read_meta(&conn, &a1).is_empty());
    }

    #[test]
    fn db_mark_stopped_keeps_existing_model() {
        let _g = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db("mark_stopped_model");
        let conn = crate::core::database::session_local().unwrap();
        let sid = "s1";
        seed_session(&conn, sid);
        let a1 = seed_msg(
            &conn,
            sid,
            "assistant",
            "a1",
            Some(r#"{"model":"orig"}"#),
            "2024-01-01 00:00:01",
        );
        drop(conn);

        db_mark_stopped(sid, "fallback").unwrap();

        let conn = crate::core::database::session_local().unwrap();
        let m = read_meta(&conn, &a1);
        assert_eq!(m.get("stopped"), Some(&json!(true)));
        // A truthy existing model is NOT overwritten.
        assert_eq!(m.get("model"), Some(&json!("orig")));
    }

    #[test]
    fn db_mark_stopped_no_assistant_is_noop() {
        let _g = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db("mark_stopped_noop");
        let conn = crate::core::database::session_local().unwrap();
        let sid = "s1";
        seed_session(&conn, sid);
        let u = seed_msg(&conn, sid, "user", "q1", None, "2024-01-01 00:00:00");
        drop(conn);

        // No assistant row -> the `if db_messages:` guard skips everything.
        db_mark_stopped(sid, "m").unwrap();

        let conn = crate::core::database::session_local().unwrap();
        assert!(read_meta(&conn, &u).is_empty());
    }

    #[test]
    fn db_update_last_meta_merges_into_last_assistant() {
        let _g = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db("update_last_meta");
        let conn = crate::core::database::session_local().unwrap();
        let sid = "s1";
        seed_session(&conn, sid);
        let a2 = seed_msg(
            &conn,
            sid,
            "assistant",
            "a2",
            Some(r#"{"keep":1}"#),
            "2024-01-01 00:00:03",
        );
        drop(conn);

        let mut update = Map::new();
        update.insert("variants".to_string(), json!(["x", "y"]));
        update.insert("keep".to_string(), json!(2)); // overwrites the existing key
        db_update_last_meta(sid, &update).unwrap();

        let conn = crate::core::database::session_local().unwrap();
        let m = read_meta(&conn, &a2);
        assert_eq!(m.get("variants"), Some(&json!(["x", "y"])));
        // `dict.update` overwrites on key collision.
        assert_eq!(m.get("keep"), Some(&json!(2)));
    }

    #[test]
    fn db_merge_last_assistant_rewrites_and_deletes_between() {
        let _g = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db("merge_last");
        let conn = crate::core::database::session_local().unwrap();
        let sid = "s1";
        seed_session(&conn, sid);
        let u1 = seed_msg(&conn, sid, "user", "q1", None, "2024-01-01 00:00:00");
        let a1 = seed_msg(&conn, sid, "assistant", "a1", None, "2024-01-01 00:00:01");
        let cont = seed_msg(
            &conn,
            sid,
            "user",
            "previous response was interrupted; continue",
            None,
            "2024-01-01 00:00:02",
        );
        let a2 = seed_msg(&conn, sid, "assistant", "a2", None, "2024-01-01 00:00:03");
        drop(conn);

        let mut merged_meta = Map::new();
        merged_meta.insert("model".to_string(), json!("m1"));
        db_merge_last_assistant(sid, "a1\n\na2", &merged_meta).unwrap();

        let conn = crate::core::database::session_local().unwrap();
        // db1 (the second-to-last assistant row) holds the merged content + metadata.
        let (content, meta): (String, Option<String>) = conn
            .query_row(
                "SELECT content, metadata FROM chat_messages WHERE id = ?1",
                rusqlite::params![a1],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap();
        assert_eq!(content, "a1\n\na2");
        assert_eq!(meta.as_deref(), Some(r#"{"model":"m1"}"#));
        // The continue user message AND the last assistant row were deleted.
        let remaining: Vec<String> = {
            let mut stmt = conn
                .prepare("SELECT id FROM chat_messages WHERE session_id = ?1 ORDER BY timestamp")
                .unwrap();
            let v = stmt
                .query_map(rusqlite::params![sid], |r| r.get::<_, String>(0))
                .unwrap()
                .collect::<rusqlite::Result<Vec<_>>>()
                .unwrap();
            v
        };
        assert_eq!(remaining, vec![u1, a1]);
        // Sanity: the deleted rows are gone.
        assert!(!remaining.contains(&cont));
        assert!(!remaining.contains(&a2));
    }

    #[test]
    fn db_merge_last_assistant_under_two_assistants_is_noop() {
        let _g = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db("merge_noop");
        let conn = crate::core::database::session_local().unwrap();
        let sid = "s1";
        seed_session(&conn, sid);
        seed_msg(&conn, sid, "user", "q1", None, "2024-01-01 00:00:00");
        let a1 = seed_msg(&conn, sid, "assistant", "a1", None, "2024-01-01 00:00:01");
        drop(conn);

        // Only one assistant row -> the `if len(ai_db) >= 2:` guard skips the rewrite.
        db_merge_last_assistant(sid, "SHOULD NOT APPEAR", &Map::new()).unwrap();

        let conn = crate::core::database::session_local().unwrap();
        let content: String = conn
            .query_row(
                "SELECT content FROM chat_messages WHERE id = ?1",
                rusqlite::params![a1],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(content, "a1");
        let count: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM chat_messages WHERE session_id = ?1",
                rusqlite::params![sid],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(count, 2);
    }
}
