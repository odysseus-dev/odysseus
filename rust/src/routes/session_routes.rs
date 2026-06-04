// routes/session_routes.rs  <- routes/session_routes.py
//! Session CRUD / lifecycle API (routes WAVE 4 — the RECONCILIATION).
//!
//! Faithful translation of `routes/session_routes.py`'s `setup_session_routes`
//! factory. This module SUPERSEDES the inline `web/mod.rs` session subset
//! (`/api/sessions`, `POST /api/session`, `GET`/`DELETE`/`PATCH /api/session/:sid`,
//! `/api/session/:sid/inject_messages`, `/api/session/:sid/archive`, and
//! `GET /api/history/:sid`) — the RECONCILE step deletes those inline handlers in
//! the same commit this router joins the aggregator.
//!
//! ## Shape (the integration substrate)
//! * `setup_session_routes() -> Router<AppState>` mirrors the Python factory. The
//!   Python `APIRouter(prefix="/api", tags=["sessions"])` means every handler
//!   carries the absolute `/api/...` path here. The captured `session_manager` /
//!   `webhook_manager` come from `State<AppState>` (`state.sessions` /
//!   `state.webhook_manager`); the `config` dict (`REQUEST_TIMEOUT`,
//!   `OPENAI_API_KEY`, `SESSIONS_FILE`) is read the same way Python reads it
//!   (`OPENAI_API_KEY` from env, `SESSIONS_FILE` from constants).
//! * Auth: the Python `_verify_session_owner(request, sid)` ownership guard maps to
//!   the F3 [`crate::routes::auth_adapter::verify_session_owner`] (403 when no
//!   user, else 404 when missing OR owned by someone else). Handlers that only call
//!   `get_current_user(request)` read `Option<Extension<CurrentUser>>` directly (the
//!   load-bearing `None` auth-disabled / single-user case).
//! * `raise HTTPException(s, d)` -> `return Err(HttpException::new(s, d))`; the dict
//!   detail (`/session/:sid` delete's `SESSION_STARRED`) maps to
//!   [`HttpException::with_detail`].
//! * Per-table ORM reads/writes (Document / GalleryImage scans, ModelEndpoint key
//!   read, DbSession column read/write, the archived browser, the auto-sort
//!   aggregates, bulk-delete) are written as module-private raw `rusqlite` here
//!   (single-writer; mirroring compare/research/memory/note routes).
//!
//! ## OMITTED — the two paths `history_routes` owns (last-include-wins)
//! `session_routes.py` and `history_routes.py` BOTH define two paths:
//! `POST /session/{sid}/compact` and `GET /api/history/{session_id}`. FastAPI's
//! last-include-wins gives both to `history_routes` (app.py include #10 > this
//! module's #4). axum `Router::merge` instead PANICS on a duplicate `method`+`path`
//! (and `:sid` vs `:session_id` conflict identically), so this module registers
//! NEITHER; `history_routes` owns both. These are the only two `session_routes.py`
//! endpoints omitted here.
//!
//! ## No remaining path collision
//! Every other route is registered exactly once here; `history_routes` (the sibling
//! wave-4 module) owns `/api/history/:session_id`, `/compact`, and the
//! `/session/:session_id/{truncate,message,...}` mutation family, which this module
//! does not touch.


use std::collections::HashSet;

use axum::extract::{Multipart, Path, Query, State};
use axum::response::{IntoResponse, Response};
use axum::routing::{delete, get, post};
use axum::{Extension, Json, Router};
use indexmap::IndexMap;
use rusqlite::OptionalExtension;
use serde::Deserialize;
use serde_json::{json, Map, Value};

use crate::routes::auth_adapter;
use crate::routes::{ApiToken, AppState, CurrentUser, HttpException};

// ---------------------------------------------------------------------------
// Router factory
// ---------------------------------------------------------------------------

/// `def setup_session_routes(session_manager, config, webhook_manager=None)` —
/// assemble the sessions router.
///
/// The Python `APIRouter(prefix="/api")` is flattened to absolute `/api/...` paths
/// here. `config` (`REQUEST_TIMEOUT`/`OPENAI_API_KEY`/`SESSIONS_FILE`) is read inside
/// the handlers from env / constants (the values app.py threads in are themselves
/// derived from env). Routes are registered in the Python source order, with the
/// static `/sessions/*` paths preceding the `/session/:sid` captures so axum's
/// matcher resolves them the way FastAPI's ordered routing does.
///
/// OMITS `/session/:sid/compact` AND `GET /api/history/:sid` (history_routes owns
/// both under last-include-wins — see the module docstring).
pub fn setup_session_routes() -> Router<AppState> {
    Router::new()
        // --- LIST ---
        .route("/api/sessions", get(list_sessions))
        // --- CREATE ---
        .route("/api/session", post(create_session))
        // --- RENAME / FOLDER / MODEL SWITCH ---
        .route("/api/session/:sid", delete(delete_session).patch(rename_session))
        // --- BULK-DELETE (static; precedes the `/session/:sid` capture above is N/A
        //     since these live under `/sessions/`, a distinct prefix) ---
        .route("/api/sessions/bulk-delete", post(bulk_delete_sessions))
        // --- DELETE ALL (admin) ---
        .route("/api/sessions/all", delete(delete_all_sessions))
        // --- ARCHIVED BROWSER ---
        .route("/api/sessions/archived", get(list_archived_sessions))
        // --- SAVE NOW ---
        .route("/api/sessions/save", post(sessions_save_now))
        // --- AUTO-SORT ---
        .route("/api/sessions/auto-sort", post(auto_sort_sessions))
        // --- INJECT MESSAGES ---
        .route("/api/session/:sid/inject_messages", post(inject_messages))
        // --- DELETE (beacon, POST) ---
        .route("/api/session/:sid/delete", post(delete_session_beacon))
        // --- ARCHIVE / UNARCHIVE ---
        .route("/api/session/:sid/archive", post(archive_session))
        .route("/api/session/:sid/unarchive", post(unarchive_session))
        // --- HISTORY ---
        // OMITTED — `GET /api/history/:sid`. BOTH `session_routes.py` (this module,
        // app.py include #4) and `history_routes.py` (include #10) register
        // `GET /api/history/{session_id}`; FastAPI last-include-wins gives it to
        // history_routes. axum `Router::merge` instead PANICS on the duplicate
        // `method`+`path` (and `:sid` vs `:session_id` conflict the same), so this
        // module does NOT register it — history_routes owns it (see the wave-4
        // reconciliation note in `routes/mod.rs::api_routers`). This is the second
        // omitted session_routes endpoint, alongside `/session/:sid/compact`.
        // --- EXPORT ---
        .route("/api/session/:sid/export", get(export_session))
        // --- OPENAI QUICK-CREATE ---
        .route("/api/session/openai", post(create_session_openai))
        // --- MARK IMPORTANT ---
        .route("/api/session/:session_id/important", post(mark_session_important))
        // --- CONTEXT INFO ---
        .route("/api/session/:session_id/context_info", get(get_context_info))
}

// ---------------------------------------------------------------------------
// GET /api/sessions
// ---------------------------------------------------------------------------

/// `GET /api/sessions` — list the current user's active (non-archived) sessions.
///
/// Reproduces the lazy incognito-ghost purge (a best-effort DB sweep of `Nobody`/
/// `Incognito` rows older than 10 minutes), then merges `session_manager`'s
/// in-memory sessions with the DB folder/token/important/timestamp/mode/count maps
/// and the document/image presence sets. Key order + the throwaway-name filter +
/// the `last_message_at` fallback cascade are preserved exactly.
async fn list_sessions(
    State(s): State<AppState>,
    tok: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    // `user = effective_user(request)`
    let user = effective_user(&tok, &user);

    // ── Lazy purge: incognito ghosts older than 10 minutes. ──
    // `try: ... except Exception: pass` — every failure is swallowed.
    let _ = (|| -> rusqlite::Result<()> {
        // `_cutoff = datetime.utcnow() - timedelta(minutes=10)`
        let cutoff = crate::pydatetime::naive_to_iso(
            crate::pydatetime::utcnow_naive() - chrono::Duration::minutes(10),
        );
        let mut conn = session_local_raw()?;
        let tx = conn.transaction()?;
        // `_ghosts = query(DbSession).filter(name in ("Nobody","Incognito"),
        //   created_at < _cutoff).all()`
        let ghost_ids: Vec<String> = {
            let mut stmt = tx.prepare(
                "SELECT id FROM sessions \
                 WHERE name IN ('Nobody', 'Incognito') AND created_at < ?1",
            )?;
            let rows = stmt.query_map(rusqlite::params![cutoff], |r| r.get::<_, String>(0))?;
            rows.filter_map(|r| r.ok()).collect()
        };
        for gid in &ghost_ids {
            // `query(_DbMsg).filter(session_id == _g.id).delete(); db.delete(_g)`
            tx.execute("DELETE FROM chat_messages WHERE session_id = ?1", [gid])?;
            tx.execute("DELETE FROM sessions WHERE id = ?1", [gid])?;
            // `session_manager.delete_session(_g.id)` (best-effort, try/except pass).
            // The Python calls the manager's delete (which also touches the DB), but
            // the row is already being removed in this tx — evicting the cache entry
            // is the observable in-memory effect. `delete_session` here would open a
            // second connection; mirror the manager-side eviction instead.
            s.sessions.evict_cached_sessions(std::slice::from_ref(gid));
        }
        // `if _ghosts: _purge_db.commit()` — only commit when there were ghosts.
        if !ghost_ids.is_empty() {
            tx.commit()?;
        }
        Ok(())
    })();

    // `user_sessions = session_manager.get_sessions_for_user(user)`
    let user_sessions = s.sessions.get_sessions_for_user(user.as_deref());

    // ── Fetch folder/token/important/timestamp/mode/count maps + doc/img sets. ──
    let conn = session_local()?;
    let mut folder_map: Map<String, Value> = Map::new();
    let mut token_map: Map<String, Value> = Map::new();
    let mut important_map: Map<String, Value> = Map::new();
    let mut created_map: Map<String, Value> = Map::new();
    let mut updated_map: Map<String, Value> = Map::new();
    let mut last_msg_map: Map<String, Value> = Map::new();
    let mut mode_map: Map<String, Value> = Map::new();
    let mut msg_count_map: Map<String, Value> = Map::new();

    // `rows = query(id, folder, total_input_tokens, total_output_tokens, is_important,
    //   created_at, updated_at, last_message_at, mode, message_count)
    //   .filter(archived == False).all()`
    {
        let mut stmt = conn
            .prepare(
                "SELECT id, folder, total_input_tokens, total_output_tokens, is_important, \
                 created_at, updated_at, last_message_at, mode, message_count \
                 FROM sessions WHERE archived = 0",
            )
            .map_err(db_500)?;
        let rows = stmt
            .query_map([], |r| {
                Ok((
                    r.get::<_, String>(0)?,             // id
                    r.get::<_, Option<String>>(1)?,     // folder
                    r.get::<_, Option<i64>>(2)?,        // total_input_tokens
                    r.get::<_, Option<i64>>(3)?,        // total_output_tokens
                    r.get::<_, Option<bool>>(4)?,       // is_important
                    r.get::<_, Option<String>>(5)?,     // created_at
                    r.get::<_, Option<String>>(6)?,     // updated_at
                    r.get::<_, Option<String>>(7)?,     // last_message_at
                    r.get::<_, Option<String>>(8)?,     // mode
                    r.get::<_, Option<i64>>(9)?,        // message_count
                ))
            })
            .map_err(db_500)?;
        for row in rows {
            let (id, folder, ti, to, imp, created, updated, last_msg, mode, count) =
                row.map_err(db_500)?;
            folder_map.insert(id.clone(), opt_str_to_json(folder.clone()));
            // `(total_input_tokens or 0) + (total_output_tokens or 0)`
            token_map.insert(id.clone(), json!(ti.unwrap_or(0) + to.unwrap_or(0)));
            // `row.is_important or False`
            important_map.insert(id.clone(), json!(imp.unwrap_or(false)));
            // `row.created_at.isoformat() if row.created_at else None`
            let created_iso = iso_or_null(created.as_deref());
            let updated_iso = iso_or_null(updated.as_deref());
            created_map.insert(id.clone(), created_iso.clone());
            updated_map.insert(id.clone(), updated_iso.clone());
            // last_message_at -> updated_at -> created_at fallback cascade.
            let last_iso = match last_msg.as_deref().filter(|s| !s.is_empty()) {
                Some(lm) => json!(crate::pydatetime::to_isoformat(lm)),
                None => {
                    if updated_iso != Value::Null {
                        updated_iso.clone()
                    } else {
                        created_iso.clone()
                    }
                }
            };
            last_msg_map.insert(id.clone(), last_iso);
            mode_map.insert(id.clone(), opt_str_to_json(mode));
            // `row.message_count or 0`
            msg_count_map.insert(id.clone(), json!(count.unwrap_or(0)));
        }
    }

    // `doc_session_ids = {r[0] for r in query(Document.session_id)
    //   .filter(is_active == True, current_content != None, trim(current_content) != "")
    //   .distinct().all()}`
    let mut doc_session_ids: HashSet<String> = HashSet::new();
    {
        let mut stmt = conn
            .prepare(
                "SELECT DISTINCT session_id FROM documents \
                 WHERE is_active = 1 AND current_content IS NOT NULL \
                   AND TRIM(current_content) != ''",
            )
            .map_err(db_500)?;
        let rows = stmt
            .query_map([], |r| r.get::<_, Option<String>>(0))
            .map_err(db_500)?;
        for row in rows {
            if let Some(sid) = row.map_err(db_500)? {
                doc_session_ids.insert(sid);
            }
        }
    }
    // `img_session_ids = {r[0] for r in query(GalleryImage.session_id)
    //   .filter(session_id != None).distinct().all()}`
    let mut img_session_ids: HashSet<String> = HashSet::new();
    {
        let mut stmt = conn
            .prepare(
                "SELECT DISTINCT session_id FROM gallery_images WHERE session_id IS NOT NULL",
            )
            .map_err(db_500)?;
        let rows = stmt
            .query_map([], |r| r.get::<_, Option<String>>(0))
            .map_err(db_500)?;
        for row in rows {
            if let Some(sid) = row.map_err(db_500)? {
                img_session_ids.insert(sid);
            }
        }
    }
    drop(conn);

    // ── Build the session list (key order + filters preserved). ──
    const THROWAWAY: &[&str] = &["Nobody", "Incognito"];
    let mut sessions: Vec<Value> = Vec::new();
    for sess in user_sessions.values() {
        // `if not s.archived and (s.name or "").strip() not in ("Nobody","Incognito")`
        if sess.archived {
            continue;
        }
        if THROWAWAY.contains(&sess.name.trim()) {
            continue;
        }
        let id = &sess.id;
        sessions.push(json!({
            "id": sess.id,
            "name": sess.name,
            "model": sess.model,
            "endpoint_url": sess.endpoint_url,
            "rag": sess.rag,
            "archived": sess.archived,
            "folder": folder_map.get(id).cloned().unwrap_or(Value::Null),
            "total_tokens": token_map.get(id).cloned().unwrap_or_else(|| json!(0)),
            "is_important": important_map.get(id).cloned().unwrap_or_else(|| json!(false)),
            "created_at": created_map.get(id).cloned().unwrap_or(Value::Null),
            "updated_at": updated_map.get(id).cloned().unwrap_or(Value::Null),
            "last_message_at": last_msg_map.get(id).cloned().unwrap_or(Value::Null),
            "has_documents": doc_session_ids.contains(id),
            "has_images": img_session_ids.contains(id),
            "mode": mode_map.get(id).cloned().unwrap_or(Value::Null),
            "message_count": msg_count_map.get(id).cloned().unwrap_or_else(|| json!(0)),
        }));
    }

    Ok(Json(Value::Array(sessions)).into_response())
}

// ---------------------------------------------------------------------------
// POST /api/session
// ---------------------------------------------------------------------------

/// `POST /api/session` — create a chat session (multipart form).
///
/// Validates / resolves the model the way Python does: `skip_validation=true`
/// trusts the caller; an empty model probes `/v1/models` and picks the first CHAT
/// model; an explicit model is verified (or basename-matched) against the live list.
/// Then `session_manager.create_session`, the auth-header resolution (direct
/// `api_key` or the `endpoint_id`'s stored key), the webhook fire, and the
/// `session_created` event.
async fn create_session(
    State(s): State<AppState>,
    tok: Option<Extension<ApiToken>>,
    user_ext: Option<Extension<CurrentUser>>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    let f = parse_form(mp).await;
    let name = form_get(&f, "name");
    let mut endpoint_url = form_get(&f, "endpoint_url");
    let model = form_get(&f, "model");
    let rag_raw = form_get_opt(&f, "rag");
    let skip_validation = form_get_opt(&f, "skip_validation");
    let api_key = form_get(&f, "api_key");
    let endpoint_id = form_get(&f, "endpoint_id");

    // `skip_val = str(skip_validation).lower() == "true"` — Python stringifies the
    // form value (absent -> "None" -> not "true").
    let skip_val = skip_validation
        .as_deref()
        .map(|v| v.to_lowercase() == "true")
        .unwrap_or(false);

    // `user = get_current_user(request)`
    let user = current_user(&user_ext);
    // `endpoint_api_key = ""; endpoint_base_url = ""`
    let mut endpoint_api_key = String::new();
    let mut endpoint_base_url = String::new();
    // `_reject_raw_endpoint_url_for_non_admin(request, user, endpoint_id, endpoint_url)`
    reject_raw_endpoint_url_for_non_admin(&s, user.as_deref(), &endpoint_id, &endpoint_url)?;

    // `if endpoint_id and endpoint_id.strip():` — resolve a saved endpoint row.
    if !endpoint_id.trim().is_empty() {
        // `q = query(ModelEndpoint).filter(id == endpoint_id.strip(), is_enabled == True)`
        // `if user: q = owner_filter(q, ModelEndpoint, user)`; `ep = q.first()`.
        match model_endpoint_enabled_owner_scoped(endpoint_id.trim(), user.as_deref())? {
            // `if not endpoint_row: raise HTTPException(400, "Model endpoint no longer exists")`
            None => {
                return Err(HttpException::new(400, "Model endpoint no longer exists"));
            }
            Some((base_url, api_key_opt)) => {
                // `endpoint_base_url = endpoint_row.base_url or ""`
                endpoint_base_url = base_url;
                // `endpoint_api_key = endpoint_row.api_key or ""`
                endpoint_api_key = api_key_opt.unwrap_or_default();
                // `endpoint_url = build_chat_url(normalize_base(endpoint_base_url))`
                endpoint_url = crate::src::endpoint_resolver::build_chat_url(
                    &crate::src::endpoint_resolver::normalize_base(Some(&endpoint_base_url)),
                );
            }
        }
    }

    // `if not endpoint_url and not skip_val: raise HTTPException(400, ...)`
    if endpoint_url.is_empty() && !skip_val {
        return Err(HttpException::new(
            400,
            "endpoint_url is required (choose from /api/models)",
        ));
    }

    let mut model_to_use = model.clone();
    // `request_api_key = api_key.strip() if api_key else ""`
    let request_api_key = api_key.trim().to_string();
    // `effective_api_key = request_api_key or endpoint_api_key`
    let effective_api_key = if !request_api_key.is_empty() {
        request_api_key.clone()
    } else {
        endpoint_api_key.clone()
    };
    // `validation_headers = None; if effective_api_key: validation_headers =
    //   build_headers(effective_api_key, endpoint_base_url or endpoint_url)`
    let validation_headers: Option<IndexMap<String, String>> = if !effective_api_key.is_empty() {
        let base = if !endpoint_base_url.is_empty() {
            endpoint_base_url.as_str()
        } else {
            endpoint_url.as_str()
        };
        Some(crate::src::endpoint_resolver::build_headers(Some(&effective_api_key), base))
    } else {
        None
    };

    if skip_val {
        // skip_validation = trust the caller, do NOT probe /v1/models.
    } else if model_to_use.is_empty() {
        // `ids = list_model_ids(endpoint_url, headers=validation_headers); if not ids: 400`
        let ids = list_model_ids_with_headers(&endpoint_url, validation_headers.as_ref()).await;
        if ids.is_empty() {
            return Err(HttpException::new(400, "Cannot reach /v1/models"));
        }
        // Default to the first CHAT model (skip embedding/tts/whisper/etc.).
        const NON_CHAT: &[&str] = &[
            "text-embedding",
            "embedding",
            "tts-",
            "whisper",
            "text-moderation",
            "moderation-",
            "dall-e",
            "rerank",
        ];
        let chat_ids: Vec<String> = ids
            .iter()
            .filter(|m| {
                let lower = m.to_lowercase();
                !NON_CHAT.iter().any(|p| lower.contains(p))
            })
            .cloned()
            .collect();
        // `(chat_ids or ids)[0]`
        model_to_use = if !chat_ids.is_empty() {
            chat_ids[0].clone()
        } else {
            ids[0].clone()
        };
    } else {
        // `req_base = os.path.basename(model_to_use.rstrip("/"))`
        let req_base = basename(&model_to_use);
        let avail = list_model_ids_with_headers(&endpoint_url, validation_headers.as_ref()).await;
        if avail.is_empty() {
            return Err(HttpException::new(400, "Cannot reach /v1/models"));
        }
        if !avail.contains(&model_to_use) {
            // `for a in avail: if basename(a) == req_base: found = a; break`
            let found = avail.iter().find(|a| basename(a) == req_base).cloned();
            match found {
                Some(found) => model_to_use = found,
                None => {
                    return Err(HttpException::new(
                        400,
                        format!("Model not found at server. Available: {}", avail.join(", ")),
                    ));
                }
            }
        }
    }

    // `sid = str(uuid.uuid4()); user = effective_user(request)` — the owner is the
    // EFFECTIVE user (token owner when paired, else the cookie user).
    let sid = uuid::Uuid::new_v4().to_string();
    let owner = effective_user(&tok, &user_ext);
    // `rag = str(rag).lower() == "true" if rag else False`
    let rag = rag_truthy(rag_raw.as_deref());

    // `session = session_manager.create_session(...)`
    let session = s
        .sessions
        .create_session(&sid, &name, &endpoint_url, &model_to_use, rag, owner.as_deref())
        .map_err(|e| HttpException::new(500, e.to_string()))?;

    // ── Set auth headers for custom API-key endpoints. ──
    // `resolved_key = request_api_key; resolved_base = endpoint_url`
    let mut resolved_key = request_api_key.clone();
    let mut resolved_base = endpoint_url.clone();
    // `if not resolved_key and endpoint_api_key: resolved_key = endpoint_api_key;
    //   resolved_base = endpoint_base_url`
    if resolved_key.is_empty() && !endpoint_api_key.is_empty() {
        resolved_key = endpoint_api_key.clone();
        resolved_base = endpoint_base_url.clone();
    }
    // `if resolved_key: session.headers = build_headers(resolved_key, resolved_base);
    //   _persist_session_headers(sid, session.headers)`
    if !resolved_key.is_empty() {
        let headers =
            crate::src::endpoint_resolver::build_headers(Some(&resolved_key), &resolved_base);
        // `session.headers = ...` (live object) then persist (headers + updated_at).
        s.sessions
            .with_session_mut(&sid, |sess| sess.headers = headers.clone());
        persist_session_headers(&sid, &headers);
    }

    // `if webhook_manager: webhook_manager.fire_and_forget("session.created", {...})`
    s.webhook_manager.fire_and_forget(
        "session.created",
        json!({ "session_id": sid, "name": session.name, "model": model_to_use }),
    );
    // `fire_event("session_created", user)`
    crate::src::event_bus::fire_event("session_created", owner.as_deref());

    // `return SessionResponse(id, name=session.name, model, rag, archived=False)`
    Ok(Json(json!({
        "id": sid,
        "name": session.name,
        "model": model_to_use,
        "rag": rag,
        "archived": false,
    }))
    .into_response())
}

// ---------------------------------------------------------------------------
// PATCH /api/session/:sid
// ---------------------------------------------------------------------------

/// `PATCH /api/session/{sid}` — rename, set folder, and/or switch model+endpoint.
///
/// `_verify_session_owner` gate first; then `session_manager.get_session` (404 on
/// KeyError). Each present field is applied: `name` via the manager, `folder` via a
/// DB column write, and a `model`+`endpoint_url` pair switches the live session
/// (optionally re-resolving auth headers from `endpoint_id`) and persists to the DB.
async fn rename_session(
    State(s): State<AppState>,
    tok: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    Path(sid): Path<String>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    // `_verify_session_owner` resolves the EFFECTIVE user internally; the model-switch
    // block below uses `get_current_user` (the raw cookie user) for the reject check +
    // owner-filtered endpoint lookup.
    let eff_user = effective_user(&tok, &user);
    let user = current_user(&user);
    // `_verify_session_owner(request, sid)` — effective-user owner gate.
    auth_adapter::verify_session_owner(&s, eff_user.as_deref(), &sid)?;
    // `session = session_manager.get_session(sid)` (KeyError -> 404).
    let session = s
        .sessions
        .get_session(&sid)
        .map_err(|_| HttpException::new(404, format!("Session {sid} not found")))?;

    let f = parse_form(mp).await;
    // Form(None) defaults: an absent field is `None` (skipped), a present empty
    // string is "" (applied). `form_get_opt` returns `None` only when absent.
    let name = form_get_opt(&f, "name");
    let folder = form_get_opt(&f, "folder");
    let model = form_get_opt(&f, "model");
    let endpoint_url = form_get_opt(&f, "endpoint_url");
    let endpoint_id = form_get_opt(&f, "endpoint_id");

    // `result = {"id": sid}`
    let mut result = Map::new();
    result.insert("id".to_string(), json!(sid));

    // `if name is not None: session_manager.update_session_name(sid, name); result["name"] = name`
    if let Some(name) = name {
        s.sessions
            .update_session_name(&sid, &name)
            .map_err(|e| HttpException::new(500, e.to_string()))?;
        result.insert("name".to_string(), json!(name));
    }

    // `if folder is not None: db_session.folder = folder if folder else None; ...`
    if let Some(folder) = folder {
        let conn = session_local()?;
        // `db_session = query(DbSession).filter(id == sid).first()`
        let exists: bool = conn
            .query_row("SELECT 1 FROM sessions WHERE id = ?1", [&sid], |_| Ok(()))
            .optional()
            .map_err(db_500)?
            .is_some();
        if exists {
            // `folder if folder else None` — empty string stores NULL.
            let folder_val: Option<String> = if folder.is_empty() { None } else { Some(folder.clone()) };
            let now = crate::pydatetime::utcnow_naive_iso();
            conn.execute(
                "UPDATE sessions SET folder = ?1, updated_at = ?2 WHERE id = ?3",
                rusqlite::params![folder_val, now, sid],
            )
            .map_err(db_500)?;
            result.insert(
                "folder".to_string(),
                folder_val.map(Value::String).unwrap_or(Value::Null),
            );
        }
    }

    // `if model is not None and endpoint_url is not None:`
    if let (Some(model), Some(mut endpoint_url)) = (model, endpoint_url) {
        // `user = get_current_user(request)` (already resolved above as `user`).
        // `_reject_raw_endpoint_url_for_non_admin(request, user, endpoint_id, endpoint_url)`
        let endpoint_id_str = endpoint_id.clone().unwrap_or_default();
        reject_raw_endpoint_url_for_non_admin(&s, user.as_deref(), &endpoint_id_str, &endpoint_url)?;

        // `endpoint_api_key = ""; endpoint_base_url = ""`
        let mut endpoint_api_key = String::new();
        let mut endpoint_base_url = String::new();
        // `if endpoint_id:` — resolve the saved endpoint (is_enabled + owner-scoped).
        if let Some(eid) = endpoint_id.as_deref().filter(|e| !e.is_empty()) {
            match model_endpoint_enabled_owner_scoped(eid, user.as_deref())? {
                // `if not ep: raise HTTPException(400, "Model endpoint no longer exists")`
                None => return Err(HttpException::new(400, "Model endpoint no longer exists")),
                Some((base_url, api_key_opt)) => {
                    // `endpoint_base_url = ep.base_url or ""; endpoint_api_key = ep.api_key or ""`
                    endpoint_base_url = base_url;
                    endpoint_api_key = api_key_opt.unwrap_or_default();
                    // `endpoint_url = build_chat_url(normalize_base(endpoint_base_url))`
                    endpoint_url = crate::src::endpoint_resolver::build_chat_url(
                        &crate::src::endpoint_resolver::normalize_base(Some(&endpoint_base_url)),
                    );
                }
            }
        }

        // `session.model = model; session.endpoint_url = endpoint_url` (live object).
        // `if endpoint_api_key: session.headers = build_headers(endpoint_api_key,
        //   endpoint_base_url) else: session.headers = {}` (header-clear).
        let new_headers: IndexMap<String, String> = if !endpoint_api_key.is_empty() {
            crate::src::endpoint_resolver::build_headers(Some(&endpoint_api_key), &endpoint_base_url)
        } else {
            IndexMap::new()
        };
        s.sessions.with_session_mut(&sid, |sess| {
            sess.model = model.clone();
            sess.endpoint_url = endpoint_url.clone();
            sess.headers = new_headers.clone();
        });

        // `db_session.model = model; db_session.endpoint_url = endpoint_url;
        //   db_session.headers = session.headers or {}; updated_at = now`
        let conn = session_local()?;
        let exists: bool = conn
            .query_row("SELECT 1 FROM sessions WHERE id = ?1", [&sid], |_| Ok(()))
            .optional()
            .map_err(db_500)?
            .is_some();
        if exists {
            let now = crate::pydatetime::utcnow_naive_iso();
            let headers_json =
                serde_json::to_string(&new_headers).unwrap_or_else(|_| "{}".to_string());
            conn.execute(
                "UPDATE sessions SET model = ?1, endpoint_url = ?2, headers = ?3, updated_at = ?4 \
                 WHERE id = ?5",
                rusqlite::params![model, endpoint_url, headers_json, now, sid],
            )
            .map_err(db_500)?;
        }
        result.insert("model".to_string(), json!(model));
        result.insert("endpoint_url".to_string(), json!(endpoint_url));
    }

    let _ = session; // `session` was loaded for the ownership/existence check.
    Ok(Json(Value::Object(result)).into_response())
}

// ---------------------------------------------------------------------------
// POST /api/session/:sid/inject_messages
// ---------------------------------------------------------------------------

/// `POST /api/session/{sid}/inject_messages` — bulk-inject messages into a session's
/// history (group-chat sync). Ownership-gated; each message is appended via
/// `session_manager` (persisting to the DB).
async fn inject_messages(
    State(s): State<AppState>,
    tok: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    Path(sid): Path<String>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    // `_verify_session_owner(request, sid)` — effective-user owner gate.
    let user = effective_user(&tok, &user);
    auth_adapter::verify_session_owner(&s, user.as_deref(), &sid)?;
    // `sess = session_manager.get_session(sid)` (KeyError -> 404).
    s.sessions
        .get_session(&sid)
        .map_err(|_| HttpException::new(404, format!("Session {sid} not found")))?;

    // `body = await request.json(); messages = body.get("messages", [])`
    let body: Value = serde_json::from_slice(&raw_body)
        .map_err(|_| HttpException::new(400, "Invalid JSON"))?;
    let messages = body
        .as_object()
        .and_then(|o| o.get("messages"))
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();

    // `for m in messages: sess.add_message(ChatMessage(m["role"], m["content"],
    //   metadata=m.get("metadata")))`
    let count = messages.len();
    for m in &messages {
        // `m["role"]` / `m["content"]` raise KeyError -> 500 if absent.
        let role = match m.get("role").and_then(Value::as_str) {
            Some(r) => r.to_string(),
            None => return Err(HttpException::new(500, "Internal Server Error")),
        };
        let content = match m.get("content").and_then(Value::as_str) {
            Some(c) => c.to_string(),
            None => return Err(HttpException::new(500, "Internal Server Error")),
        };
        // `metadata=m.get("metadata")` — absent / null -> None.
        let metadata = m.get("metadata").and_then(|v| match v {
            Value::Object(o) => Some(o.clone()),
            _ => None,
        });
        s.sessions
            .add_message(&sid, crate::core::models::ChatMessage::new(role, content, metadata))
            .map_err(|e| HttpException::new(500, e.to_string()))?;
    }
    // `session_manager.save_sessions()` is a no-op (DB-backed).
    Ok(Json(json!({ "ok": true, "count": count })).into_response())
}

// ---------------------------------------------------------------------------
// POST /api/session/:sid/delete  (beacon)
// ---------------------------------------------------------------------------

/// `POST /api/session/{sid}/delete` — delete via POST (navigator.sendBeacon on page
/// close). Delegates to the shared delete path.
async fn delete_session_beacon(
    state: State<AppState>,
    tok: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    sid: Path<String>,
) -> Result<Response, HttpException> {
    // `return delete_session(request, sid)`
    delete_session(state, tok, user, sid).await
}

// ---------------------------------------------------------------------------
// POST /api/sessions/bulk-delete
// ---------------------------------------------------------------------------

/// `POST /api/sessions/bulk-delete` — delete multiple sessions (compare cleanup via
/// sendBeacon). Each id is ownership-checked + deleted best-effort; any per-id
/// failure is swallowed. Always returns `{"deleted": len(ids)}` (the provided count,
/// not the succeeded count).
async fn bulk_delete_sessions(
    State(s): State<AppState>,
    tok: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    // `_verify_session_owner(request, sid, session_manager)` resolves the EFFECTIVE user.
    let user = effective_user(&tok, &user);
    // `try: body = await request.json(); ids = body.get("ids", []) except: ids = []`
    let ids: Vec<Value> = serde_json::from_slice::<Value>(&raw_body)
        .ok()
        .and_then(|v| v.as_object().and_then(|o| o.get("ids").cloned()))
        .and_then(|v| match v {
            Value::Array(a) => Some(a),
            _ => None,
        })
        .unwrap_or_default();

    // `for sid in ids: try: _verify_session_owner; delete; DELETE rows; except: pass`
    for sid_val in &ids {
        let sid = match sid_val.as_str() {
            Some(s) => s,
            None => continue,
        };
        // The whole per-id block is wrapped in `try/except: pass`.
        // `_verify_session_owner(request, sid, session_manager)` — WITH the ghost fallback.
        let owns = verify_session_owner_with_ghost(&s, user.as_deref(), sid).is_ok();
        if !owns {
            continue;
        }
        let _ = s.sessions.delete_session(sid);
        if let Ok(mut conn) = session_local_raw() {
            let _ = (|| -> rusqlite::Result<()> {
                let tx = conn.transaction()?;
                tx.execute("DELETE FROM chat_messages WHERE session_id = ?1", [sid])?;
                tx.execute("DELETE FROM sessions WHERE id = ?1", [sid])?;
                tx.commit()
            })();
        }
    }

    // `return {"deleted": len(ids)}` — counts ALL provided ids.
    Ok(Json(json!({ "deleted": ids.len() })).into_response())
}

// ---------------------------------------------------------------------------
// DELETE /api/session/:sid
// ---------------------------------------------------------------------------

/// `DELETE /api/session/{sid}` — permanently delete a session and its messages.
///
/// Ownership-gated; blocks deletion of a starred (`is_important`) session with a
/// `403 {"error": "SESSION_STARRED", ...}` dict detail; then
/// `session_manager.delete_session` (404 when the row is gone, 500 on an unexpected
/// error).
async fn delete_session(
    State(s): State<AppState>,
    tok: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    Path(sid): Path<String>,
) -> Result<Response, HttpException> {
    // `_verify_session_owner(request, sid, session_manager)` resolves the EFFECTIVE user.
    let user = effective_user(&tok, &user);
    // `_verify_session_owner(request, sid, session_manager)` — WITH the ghost fallback.
    verify_session_owner_with_ghost(&s, user.as_deref(), &sid)?;

    // Inner `try/except HTTPException: raise / except Exception: 500`.
    let inner = (|| -> Result<Response, HttpException> {
        // `db_sess = query(DbSession).filter(id == sid).first(); if db_sess and
        //   db_sess.is_important: raise HTTPException(403, detail={...})`
        let conn = session_local()?;
        let is_important: Option<bool> = conn
            .query_row(
                "SELECT is_important FROM sessions WHERE id = ?1",
                [&sid],
                |r| r.get::<_, Option<bool>>(0),
            )
            .optional()
            .map_err(db_500)?
            .flatten();
        drop(conn);
        if is_important == Some(true) {
            return Err(HttpException::with_detail(
                403,
                json!({
                    "error": "SESSION_STARRED",
                    "message": "Unstar the session before deleting it",
                }),
            ));
        }
        // `if session_manager.delete_session(sid): return {"status": "deleted"}
        //   else: raise HTTPException(404, "Session not found")`
        if s.sessions.delete_session(&sid) {
            Ok(Json(json!({ "status": "deleted" })).into_response())
        } else {
            Err(HttpException::new(404, "Session not found"))
        }
    })();

    // `except HTTPException: raise` — propagate the 403/404 unchanged. There is no
    // unexpected-exception path here (the raw-SQL helpers map their own errors to a
    // 500 already), so the Python's outer `except Exception -> 500` is unreachable
    // on this branch and we surface `inner` directly.
    inner
}

// ---------------------------------------------------------------------------
// DELETE /api/sessions/all  (admin)
// ---------------------------------------------------------------------------

/// `DELETE /api/sessions/all` — admin-only: permanently delete ALL sessions and
/// their messages. `require_admin` gate; one transaction wipes both tables; the
/// in-memory cache is cleared. Returns `{"status": "deleted", "count": <n>}`.
async fn delete_all_sessions(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    headers: axum::http::HeaderMap,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    // `require_admin(request)` (core/middleware).
    let internal = headers
        .get("X-Odysseus-Internal-Token")
        .and_then(|v| v.to_str().ok());
    auth_adapter::require_admin(&s, internal, user.as_deref())?;

    let mut conn = session_local()?;
    let result = (|| -> rusqlite::Result<i64> {
        let tx = conn.transaction()?;
        // `count = query(DbSession).count()`
        let count: i64 = tx.query_row("SELECT COUNT(*) FROM sessions", [], |r| r.get(0))?;
        // `query(DbChatMessage).delete(); query(DbSession).delete(); commit`
        tx.execute("DELETE FROM chat_messages", [])?;
        tx.execute("DELETE FROM sessions", [])?;
        tx.commit()?;
        Ok(count)
    })();
    match result {
        Ok(count) => {
            // `session_manager.sessions.clear()`
            s.sessions.clear_sessions();
            crate::pylog::info(&format!("Admin deleted all {count} sessions"));
            Ok(Json(json!({ "status": "deleted", "count": count })).into_response())
        }
        // `except Exception: db.rollback(); raise HTTPException(500, ...)`
        Err(e) => {
            crate::pylog::error(&format!("Error deleting all sessions: {e}"));
            Err(HttpException::new(500, "Failed to delete sessions"))
        }
    }
}

// ---------------------------------------------------------------------------
// POST /api/session/:sid/archive
// ---------------------------------------------------------------------------

/// `POST /api/session/{sid}/archive` — archive a session (keep data, drop from the
/// active list). Ownership-gated; `get_session` existence check; DB `archived = 1`
/// + live cache flip.
async fn archive_session(
    State(s): State<AppState>,
    tok: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    Path(sid): Path<String>,
) -> Result<Response, HttpException> {
    // `_verify_session_owner(request, sid)` — effective-user owner gate.
    let user = effective_user(&tok, &user);
    auth_adapter::verify_session_owner(&s, user.as_deref(), &sid)?;

    // `try: session_manager.get_session(sid) ... except KeyError: raise 404 ('sid')`
    // NOTE the Python's KeyError arm uses single quotes: `f"Session '{sid}' not found"`.
    if s.sessions.get_session(&sid).is_err() {
        return Err(HttpException::new(404, format!("Session '{sid}' not found")));
    }

    // `db_session = query(DbSession).filter(id == sid).first()`
    let conn = session_local()?;
    let exists: bool = conn
        .query_row("SELECT 1 FROM sessions WHERE id = ?1", [&sid], |_| Ok(()))
        .optional()
        .map_err(db_500)?
        .is_some();
    if !exists {
        // `else: raise HTTPException(404, f"Session {sid} not found")` (double-quote form).
        return Err(HttpException::new(404, format!("Session {sid} not found")));
    }
    // `db_session.archived = True; updated_at = now; commit`
    let now = crate::pydatetime::utcnow_naive_iso();
    let res = conn.execute(
        "UPDATE sessions SET archived = 1, updated_at = ?1 WHERE id = ?2",
        rusqlite::params![now, sid],
    );
    if let Err(e) = res {
        // `except Exception: db.rollback(); raise HTTPException(500, ...)`
        crate::pylog::error(&format!("Error archiving session {sid}: {e}"));
        return Err(HttpException::new(500, "Failed to archive session"));
    }
    // `if sid in session_manager.sessions: session_manager.sessions[sid].archived = True`
    s.sessions.with_session_mut(&sid, |sess| sess.archived = true);
    crate::pylog::info(&format!("Archived session {sid}"));
    Ok(Json(json!({ "status": "archived" })).into_response())
}

// ---------------------------------------------------------------------------
// POST /api/session/:sid/unarchive
// ---------------------------------------------------------------------------

/// `POST /api/session/{sid}/unarchive` — restore an archived session to the active
/// list. Ownership-gated; DB `archived = 0`; reloads the row into the manager (live
/// flip if cached, else `_load_session_from_db`).
async fn unarchive_session(
    State(s): State<AppState>,
    tok: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    Path(sid): Path<String>,
) -> Result<Response, HttpException> {
    // `_verify_session_owner(request, sid)` — effective-user owner gate.
    let user = effective_user(&tok, &user);
    auth_adapter::verify_session_owner(&s, user.as_deref(), &sid)?;

    let conn = session_local()?;
    // `db_session = query(DbSession).filter(id == sid).first(); if not db_session: 404`
    let exists: bool = conn
        .query_row("SELECT 1 FROM sessions WHERE id = ?1", [&sid], |_| Ok(()))
        .optional()
        .map_err(db_500)?
        .is_some();
    if !exists {
        return Err(HttpException::new(404, format!("Session {sid} not found")));
    }
    // `db_session.archived = False; updated_at = now; commit`
    let now = crate::pydatetime::utcnow_naive_iso();
    let res = conn.execute(
        "UPDATE sessions SET archived = 0, updated_at = ?1 WHERE id = ?2",
        rusqlite::params![now, sid],
    );
    if let Err(e) = res {
        // `except Exception: db.rollback(); raise HTTPException(500, ...)`
        crate::pylog::error(&format!("Error unarchiving session {sid}: {e}"));
        return Err(HttpException::new(500, "Failed to unarchive session"));
    }
    drop(conn);

    // `try: if sid in session_manager.sessions: session_manager.sessions[sid].archived = False
    //   else: session_manager._load_session_from_db(sid) except: pass`
    let flipped = s
        .sessions
        .with_session_mut(&sid, |sess| sess.archived = false)
        .is_some();
    if !flipped {
        let _ = s.sessions._load_session_from_db(&sid);
    }
    Ok(Json(json!({ "status": "unarchived" })).into_response())
}

// ---------------------------------------------------------------------------
// GET /api/sessions/archived
// ---------------------------------------------------------------------------

/// `search` / `offset` / `limit` / `sort` / `model` query params for the archived
/// browser. Defaults mirror the Python signature (`""`, `0`, `20`, `"recent"`, `""`).
#[derive(Debug, Deserialize)]
struct ArchivedQuery {
    #[serde(default)]
    search: String,
    #[serde(default)]
    offset: i64,
    #[serde(default = "default_limit")]
    limit: i64,
    #[serde(default = "default_sort")]
    sort: String,
    #[serde(default)]
    model: String,
}

fn default_limit() -> i64 {
    20
}
fn default_sort() -> String {
    "recent".to_string()
}

/// `GET /api/sessions/archived` — list archived sessions for the archive browser.
///
/// `effective_user` is REQUIRED (403 when absent); the query is owner-scoped, with
/// optional ILIKE name search (escaped) + escaped contains-match model filter, a 4-way
/// sort map, and offset/limit paging. Returns `{"sessions": [...], "total": <n>}`.
async fn list_archived_sessions(
    State(_s): State<AppState>,
    tok: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<ArchivedQuery>,
) -> Result<Response, HttpException> {
    let user = effective_user(&tok, &user);
    let conn = session_local()?;

    // Build the WHERE clause. `q.filter(archived == True)` then owner / search / model.
    let mut wheres: Vec<String> = vec!["archived = 1".to_string()];
    let mut params: Vec<Box<dyn rusqlite::ToSql>> = Vec::new();

    // `if not user: raise HTTPException(403, "Authentication required")`
    let user = match user.filter(|u| !u.is_empty()) {
        Some(u) => u,
        None => return Err(HttpException::new(403, "Authentication required")),
    };
    wheres.push(format!("owner = ?{}", params.len() + 1));
    params.push(Box::new(user));

    // `if search: q.filter(name.ilike(f"%{safe_search}%", escape='\\'))`
    if !q.search.is_empty() {
        // `safe_search = search.replace('%', r'\%').replace('_', r'\_')`
        let safe = q.search.replace('%', r"\%").replace('_', r"\_");
        wheres.push(format!(
            "name LIKE ?{} ESCAPE '\\'",
            params.len() + 1
        ));
        params.push(Box::new(format!("%{safe}%")));
    }
    // `if model: q.filter(model.ilike(f"%{safe_model}%", escape='\\'))` — CONTAINS match
    // (was a SUFFIX-only `f"%{model}"`, which dropped "gpt-4o" when filtering "gpt-4" and
    // over-matched on shared suffixes; it also left LIKE wildcards unescaped).
    if !q.model.is_empty() {
        // `safe_model = model.replace('%', r'\%').replace('_', r'\_')`
        let safe_model = q.model.replace('%', r"\%").replace('_', r"\_");
        wheres.push(format!("model LIKE ?{} ESCAPE '\\'", params.len() + 1));
        params.push(Box::new(format!("%{safe_model}%")));
    }

    let where_sql = format!("WHERE {}", wheres.join(" AND "));

    // `total = q.count()`
    let count_sql = format!("SELECT COUNT(*) FROM sessions {where_sql}");
    let total: i64 = {
        let param_refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|p| p.as_ref()).collect();
        conn.query_row(&count_sql, param_refs.as_slice(), |r| r.get(0))
            .map_err(db_500)?
    };

    // `sort_map = {...}; order = sort_map.get(sort, updated_at.desc())`
    // SQLite sorts NULLs FIRST by default; `nulls_last()` on most-messages is honored
    // with an explicit `IS NULL` key. The other orders mirror the column defaults.
    let order = match q.sort.as_str() {
        "recent" => "ORDER BY updated_at DESC",
        "oldest" => "ORDER BY updated_at ASC",
        "most-messages" => "ORDER BY message_count IS NULL, message_count DESC",
        "alpha" => "ORDER BY name ASC",
        _ => "ORDER BY updated_at DESC",
    };

    // `rows = q.order_by(order).offset(offset).limit(limit).all()`
    let sql = format!(
        "SELECT id, name, model, message_count, created_at, updated_at, is_important \
         FROM sessions {where_sql} {order} LIMIT ?{} OFFSET ?{}",
        params.len() + 1,
        params.len() + 2,
    );
    params.push(Box::new(q.limit));
    params.push(Box::new(q.offset));

    let mut stmt = conn.prepare(&sql).map_err(db_500)?;
    let param_refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|p| p.as_ref()).collect();
    let sessions: Vec<Value> = stmt
        .query_map(param_refs.as_slice(), |r| {
            let id: String = r.get(0)?;
            let name: Option<String> = r.get(1)?;
            let model: Option<String> = r.get(2)?;
            let message_count: Option<i64> = r.get(3)?;
            let created_at: Option<String> = r.get(4)?;
            let updated_at: Option<String> = r.get(5)?;
            let is_important: Option<bool> = r.get(6)?;
            Ok(json!({
                "id": id,
                "name": name,
                "model": model,
                // `s.message_count or 0`
                "message_count": message_count.unwrap_or(0),
                "created_at": iso_or_null(created_at.as_deref()),
                "updated_at": iso_or_null(updated_at.as_deref()),
                "is_important": is_important,
            }))
        })
        .map_err(db_500)?
        .collect::<rusqlite::Result<Vec<_>>>()
        .map_err(db_500)?;

    Ok(Json(json!({ "sessions": sessions, "total": total })).into_response())
}

// `GET /api/history/{sid}` is NOT defined here — see the omission note in
// `setup_session_routes`: `history_routes.py` (app.py include #10) owns it under
// FastAPI last-include-wins, and axum `Router::merge` would panic on the duplicate
// `method`+`path`. The handler lives in `history_routes::get_session_history`.

// ---------------------------------------------------------------------------
// GET /api/session/:sid/export
// ---------------------------------------------------------------------------

/// `fmt` / `filename` query params for export (defaults `"md"` / `""`).
#[derive(Debug, Deserialize)]
struct ExportQuery {
    #[serde(default = "default_fmt")]
    fmt: String,
    #[serde(default)]
    filename: String,
}

fn default_fmt() -> String {
    "md".to_string()
}

/// `GET /api/session/{sid}/export` — export conversation history as a downloadable
/// file (`md` / `txt` / `json` / `html`). Ownership-gated; builds the body + the
/// `Content-Disposition` attachment header for the chosen format.
async fn export_session(
    State(s): State<AppState>,
    tok: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    Path(sid): Path<String>,
    Query(q): Query<ExportQuery>,
) -> Result<Response, HttpException> {
    // `_verify_session_owner(request, sid)` — effective-user owner gate.
    let user = effective_user(&tok, &user);
    auth_adapter::verify_session_owner(&s, user.as_deref(), &sid)?;
    let session = s
        .sessions
        .get_session(&sid)
        .map_err(|_| HttpException::new(404, format!("Session {sid} not found")))?;

    // `safe_name = re.sub(r'[^\w\-_]', '_', session.name)` — `\w` is `[A-Za-z0-9_]`
    // plus Unicode word chars (Python `re` is Unicode by default).
    let safe_name: String = session
        .name
        .chars()
        .map(|c| {
            if c.is_alphanumeric() || c == '_' || c == '-' {
                c
            } else {
                '_'
            }
        })
        .collect();
    // `timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')` — LOCAL now.
    let timestamp = chrono::Local::now().format("%Y%m%d_%H%M%S").to_string();
    // `filename = _sanitize_export_filename(filename)` — SECURITY: scrub the
    // caller-supplied filename to `[A-Za-z0-9._-]` (max 128) so it cannot inject CRLF /
    // extra `Content-Disposition` directives or traverse paths.
    let filename = sanitize_export_filename(&q.filename);

    let (content, media_type, out_name) = match q.fmt.as_str() {
        "json" => {
            // `data = {name, model, exported: datetime.now().isoformat(),
            //   messages: [{role, content}]}`
            let data = json!({
                "name": session.name,
                "model": session.model,
                "exported": chrono::Local::now().naive_local().format("%Y-%m-%dT%H:%M:%S%.6f").to_string(),
                "messages": session.history.iter().map(|m| json!({
                    "role": m.role, "content": m.content
                })).collect::<Vec<_>>(),
            });
            // `json.dumps(data, indent=2, ensure_ascii=False)`
            let body = serde_json::to_string_pretty(&data).unwrap_or_else(|_| "{}".to_string());
            let out = filename_or(&filename, &safe_name, &timestamp, "json");
            (body, "application/json", out)
        }
        "txt" => {
            // `lines.append(f"[{role.upper()}]"); lines.append(content); lines.append("")`
            let mut lines: Vec<String> = Vec::new();
            for m in &session.history {
                lines.push(format!("[{}]", m.role.to_uppercase()));
                lines.push(m.content.clone());
                lines.push(String::new());
            }
            let out = filename_or(&filename, &safe_name, &timestamp, "txt");
            (lines.join("\n"), "text/plain", out)
        }
        "html" => {
            // `safe_title = html.escape(session.name or "")` — SECURITY: stored-XSS fix.
            // The session name is attacker-controllable (auto-naming / rename), and the
            // export was injecting it RAW into `<title>` / `<h1>`. Escape it once.
            let safe_title = html_escape(&session.name);
            let mut html_parts: Vec<String> = vec![
                "<!DOCTYPE html><html><head>".to_string(),
                format!("<meta charset='utf-8'><title>{safe_title}</title>"),
                "<style>body{font-family:monospace;max-width:800px;margin:2rem auto;padding:0 1rem;background:#111;color:#ddd}".to_string(),
                ".msg{margin:1rem 0;padding:0.8rem;border-radius:6px;border:1px solid #333}".to_string(),
                ".user{background:#1a1a2e}.ai{background:#1a2e1a}".to_string(),
                ".role{font-weight:bold;margin-bottom:0.4rem;opacity:0.7;text-transform:uppercase;font-size:0.85em}".to_string(),
                "pre{background:#000;padding:0.5rem;border-radius:4px;overflow-x:auto}</style></head><body>".to_string(),
                format!("<h1>{safe_title}</h1>"),
            ];
            for m in &session.history {
                let cls = if m.role == "user" { "user" } else { "ai" };
                // `content.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")`
                let content = m
                    .content
                    .replace('&', "&amp;")
                    .replace('<', "&lt;")
                    .replace('>', "&gt;")
                    .replace('\n', "<br>");
                html_parts.push(format!(
                    "<div class=\"msg {cls}\"><div class=\"role\">{}</div>{content}</div>",
                    m.role
                ));
            }
            html_parts.push("</body></html>".to_string());
            let out = filename_or(&filename, &safe_name, &timestamp, "html");
            (html_parts.join("\n"), "text/html", out)
        }
        _ => {
            // Default: markdown.
            let mut markdown_lines: Vec<String> = Vec::new();
            markdown_lines.push(format!("# Conversation: {}", session.name));
            markdown_lines.push(format!(
                "*Exported on: {}*",
                chrono::Local::now().format("%Y-%m-%d %H:%M:%S")
            ));
            markdown_lines.push(format!("*Model: {}*", session.model));
            markdown_lines.push("\n---\n".to_string());
            for message in &session.history {
                markdown_lines.push(format!("### {}", message.role.to_uppercase()));
                markdown_lines.push(format!("{}\n", message.content));
                markdown_lines.push("---\n".to_string());
            }
            // `if len(markdown_lines) > 3: markdown_lines.pop()`
            if markdown_lines.len() > 3 {
                markdown_lines.pop();
            }
            let out = filename_or(&filename, &safe_name, &timestamp, "md");
            (markdown_lines.join("\n"), "text/markdown", out)
        }
    };

    // Build the Response with the attachment header (FastAPI `Response(...)`).
    let resp = Response::builder()
        .header(axum::http::header::CONTENT_TYPE, media_type)
        .header(
            axum::http::header::CONTENT_DISPOSITION,
            format!("attachment; filename={out_name}"),
        )
        .body(axum::body::Body::from(content))
        .map_err(|_| HttpException::new(500, "Internal Server Error"))?;
    Ok(resp)
}

// ---------------------------------------------------------------------------
// POST /api/sessions/save
// ---------------------------------------------------------------------------

/// `POST /api/sessions/save` — force a session flush. `effective_user` required
/// (401 when absent). `save_sessions()` is a no-op (DB-backed); returns
/// `{"ok": True, "path": SESSIONS_FILE}`.
async fn sessions_save_now(
    State(s): State<AppState>,
    tok: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    // `user = effective_user(request); if not user: raise HTTPException(401, ...)`
    let user = effective_user(&tok, &user);
    if user.as_deref().filter(|u| !u.is_empty()).is_none() {
        return Err(HttpException::new(401, "Not authenticated"));
    }
    s.sessions.save_sessions();
    // `return {"ok": True, "path": SESSIONS_FILE}` — `constants.SESSIONS_FILE`
    // (`os.path.join(DATA_DIR, "sessions.json")`).
    Ok(Json(json!({ "ok": true, "path": &*crate::core::constants::SESSIONS_FILE })).into_response())
}

// ---------------------------------------------------------------------------
// POST /api/session/openai
// ---------------------------------------------------------------------------

/// `POST /api/session/openai` — quick-create an OpenAI session using the server's
/// `OPENAI_API_KEY`. 400 when the key is unset; else creates a session pointed at
/// the OpenAI chat-completions URL with the bearer header. Returns
/// `{"id", "name": "", "model"}`.
async fn create_session_openai(
    State(s): State<AppState>,
    tok: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    let f = parse_form(mp).await;
    // Form defaults: name="New Chat (OpenAI)" (unused — create passes ""), model="gpt-4o".
    let model = form_get_with_default(&f, "model", "gpt-4o");
    let rag_raw = form_get_opt(&f, "rag");

    // `if not OPENAI_API_KEY: raise HTTPException(400, "Server missing OPENAI_API_KEY")`
    let openai_key = crate::pyos::getenv("OPENAI_API_KEY", "");
    if openai_key.is_empty() {
        return Err(HttpException::new(400, "Server missing OPENAI_API_KEY"));
    }

    let sid = uuid::Uuid::new_v4().to_string();
    // `user = effective_user(request)` — owner attribution.
    let owner = effective_user(&tok, &user);
    // `rag = str(rag).lower() == "true"` (note: NO `if rag` guard here, unlike create).
    let rag = rag_raw.as_deref().map(|v| v.to_lowercase() == "true").unwrap_or(false);

    // `session = create_session(name="", endpoint_url="https://api.openai.com/v1/chat/completions",
    //   model, rag, owner=user)`
    s.sessions
        .create_session(
            &sid,
            "",
            "https://api.openai.com/v1/chat/completions",
            &model,
            rag,
            owner.as_deref(),
        )
        .map_err(|e| HttpException::new(500, e.to_string()))?;
    // `session.headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}; save_sessions()`
    let mut headers = IndexMap::new();
    headers.insert("Authorization".to_string(), format!("Bearer {openai_key}"));
    s.sessions.update_session_headers(&sid, headers);

    // `fire_event("session_created", user)`
    crate::src::event_bus::fire_event("session_created", owner.as_deref());

    // `return {"id": sid, "name": "", "model": model}`
    Ok(Json(json!({ "id": sid, "name": "", "model": model })).into_response())
}

// ---------------------------------------------------------------------------
// POST /api/session/:session_id/important
// ---------------------------------------------------------------------------

/// `POST /api/session/{session_id}/important` — mark/unmark a session as important
/// (protects it from auto-cleanup). The `important` form field defaults to `True`.
/// Ownership-gated; `get_session` existence check; DB `is_important` write + live
/// cache flip.
async fn mark_session_important(
    State(s): State<AppState>,
    tok: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    Path(session_id): Path<String>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    // `_verify_session_owner(request, session_id)` — effective-user owner gate.
    let user = effective_user(&tok, &user);
    auth_adapter::verify_session_owner(&s, user.as_deref(), &session_id)?;

    let f = parse_form(mp).await;
    // `important: bool = Form(True)` — absent -> True; present parses pydantic bool.
    let important = match form_get_opt(&f, "important") {
        Some(v) => parse_form_bool(&v)?,
        None => true,
    };

    // `try: session_manager.get_session(session_id) ... except KeyError: 404`
    if s.sessions.get_session(&session_id).is_err() {
        return Err(HttpException::new(404, format!("Session {session_id} not found")));
    }

    let conn = session_local()?;
    let exists: bool = conn
        .query_row("SELECT 1 FROM sessions WHERE id = ?1", [&session_id], |_| Ok(()))
        .optional()
        .map_err(db_500)?
        .is_some();
    if !exists {
        // `else: raise HTTPException(404, f"Session {session_id} not found")`
        return Err(HttpException::new(404, format!("Session {session_id} not found")));
    }
    let now = crate::pydatetime::utcnow_naive_iso();
    let res = conn.execute(
        "UPDATE sessions SET is_important = ?1, updated_at = ?2 WHERE id = ?3",
        rusqlite::params![important, now, session_id],
    );
    if let Err(e) = res {
        // `except Exception: db.rollback(); raise HTTPException(500, ...)`
        crate::pylog::error(&format!("Error updating session {session_id} importance: {e}"));
        return Err(HttpException::new(500, "Failed to update session importance"));
    }
    // `if session_id in session_manager.sessions: ...is_important = important`
    s.sessions
        .with_session_mut(&session_id, |sess| sess.is_important = important);
    Ok(Json(json!({ "status": "success", "is_important": important })).into_response())
}

// ---------------------------------------------------------------------------
// POST /api/sessions/auto-sort
// ---------------------------------------------------------------------------

/// `skip_llm` query flag for auto-sort (default `false`).
#[derive(Debug, Deserialize)]
struct AutoSortQuery {
    #[serde(default)]
    skip_llm: bool,
}

/// `POST /api/sessions/auto-sort` — AI-categorize sessions into folders.
///
/// Phase 1 deletes empty / throwaway sessions (driven by two aggregate count
/// queries); Phase 2 (skipped when `skip_llm=true`) asks the utility/task LLM to
/// group the unfiled batch into folders and applies the assignments. The LLM call
/// uses the same endpoint cascade as the Python (`resolve_task_endpoint` ->
/// `_pick_endpoint_for_sort`), via the async `llm_call_async` (Python's sync
/// `llm_call` in an async runtime).
// mirrors session_routes.py:838-848 — the throwaway-cleanup if/elif chain has
// three deliberately-identical bodies (`should_delete = true; deleted_throwaway
// += 1`) under distinct conditions; keep the 1:1 shape rather than collapsing.
#[allow(clippy::if_same_then_else)]
async fn auto_sort_sessions(
    State(s): State<AppState>,
    tok: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<AutoSortQuery>,
) -> Result<Response, HttpException> {
    // `user = effective_user(request)`
    let user = effective_user(&tok, &user);
    // `user_sessions = session_manager.get_sessions_for_user(user)`
    let mut user_sessions = s.sessions.get_sessions_for_user(user.as_deref());

    // ── Phase 1: delete empty + throwaway sessions. ──
    const THROWAWAY_NAMES: &[&str] = &[
        "test", "testing", "asdf", "asd", "hello", "hi", "hey", "yo", "sup", "hola",
        "hii", "hiii", "heyo", "foo", "bar", "baz", "tmp", "temp", "scratch",
        "untitled", "new chat", "delete", "remove", "junk", "trash", "xxx", "abc",
        "qwerty", "blah", "stuff", "whatever", "idk", "ok", "lol", "bruh", "hmm",
        "hm", "meh",
    ];
    const THROWAWAY_MAX_MESSAGES: i64 = 4;

    let mut deleted_empty = 0i64;
    let mut deleted_throwaway = 0i64;
    let mut folder_map: Map<String, Value> = Map::new();

    {
        let mut conn = session_local_raw().map_err(db_500)?;
        // `rows = query(DbSession).filter(archived == False, DbSession.owner == user).all()`
        // OWNER-SCOPED: `DbSession.owner == user` (a strict equality, NOT owner_filter) —
        // for a `None`/empty user this is `owner IS NULL` (single-user), else `owner = ?`.
        // Without this the cleanup scanned (and could delete) OTHER users' sessions.
        struct SortRow {
            id: String,
            name: Option<String>,
            folder: Option<String>,
            is_important: bool,
        }
        let owner = user.as_deref().filter(|u| !u.is_empty());
        let map_sort_row = |r: &rusqlite::Row| -> rusqlite::Result<SortRow> {
            Ok(SortRow {
                id: r.get(0)?,
                name: r.get(1)?,
                folder: r.get(2)?,
                is_important: r.get::<_, Option<bool>>(3)?.unwrap_or(false),
            })
        };
        let rows: Vec<SortRow> = match owner {
            Some(u) => {
                let mut stmt = conn
                    .prepare(
                        "SELECT id, name, folder, is_important FROM sessions \
                         WHERE archived = 0 AND owner = ?1",
                    )
                    .map_err(db_500)?;
                let it = stmt.query_map([u], map_sort_row).map_err(db_500)?;
                it.filter_map(|r| r.ok()).collect()
            }
            None => {
                let mut stmt = conn
                    .prepare(
                        "SELECT id, name, folder, is_important FROM sessions \
                         WHERE archived = 0 AND owner IS NULL",
                    )
                    .map_err(db_500)?;
                let it = stmt.query_map([], map_sort_row).map_err(db_500)?;
                it.filter_map(|r| r.ok()).collect()
            }
        };
        // `folder_map = {r.id: r.folder for r in rows}`
        for row in &rows {
            folder_map.insert(row.id.clone(), opt_str_to_json(row.folder.clone()));
        }
        // `_counts = dict(query(session_id, count(id)).group_by(session_id).all())`
        let counts: Map<String, Value> = {
            let mut stmt = conn
                .prepare("SELECT session_id, COUNT(id) FROM chat_messages GROUP BY session_id")
                .map_err(db_500)?;
            let it = stmt
                .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))
                .map_err(db_500)?;
            let mut m = Map::new();
            for row in it {
                let (sid, c) = row.map_err(db_500)?;
                m.insert(sid, json!(c));
            }
            m
        };
        // `_asst_counts = dict(query(...).filter(role == "assistant").group_by(...).all())`
        let asst_counts: Map<String, Value> = {
            let mut stmt = conn
                .prepare(
                    "SELECT session_id, COUNT(id) FROM chat_messages \
                     WHERE role = 'assistant' GROUP BY session_id",
                )
                .map_err(db_500)?;
            let it = stmt
                .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))
                .map_err(db_500)?;
            let mut m = Map::new();
            for row in it {
                let (sid, c) = row.map_err(db_500)?;
                m.insert(sid, json!(c));
            }
            m
        };

        let mut to_delete: Vec<String> = Vec::new();
        for row in &rows {
            // `if getattr(row, 'is_important', False): continue`
            if row.is_important {
                continue;
            }
            let name_trim = row.name.as_deref().unwrap_or("").trim().to_string();
            // `if (row.name or "").strip() == "Incognito": delete; continue`
            if name_trim == "Incognito" {
                deleted_throwaway += 1;
                to_delete.push(row.id.clone());
                continue;
            }
            // `msg_count = _counts.get(row.id, 0)`
            let msg_count = counts.get(&row.id).and_then(Value::as_i64).unwrap_or(0);
            let mut should_delete = false;
            if msg_count == 0 {
                should_delete = true;
                deleted_empty += 1;
            } else if msg_count <= THROWAWAY_MAX_MESSAGES {
                let name = name_trim.to_lowercase();
                // `first_msg = query(content).filter(session_id, role=="user")
                //   .order_by(timestamp).first()`
                let first_text: String = conn
                    .query_row(
                        "SELECT content FROM chat_messages \
                         WHERE session_id = ?1 AND role = 'user' ORDER BY timestamp LIMIT 1",
                        [&row.id],
                        |r| r.get::<_, Option<String>>(0),
                    )
                    .optional()
                    .map_err(db_500)?
                    .flatten()
                    .unwrap_or_default()
                    .trim()
                    .to_lowercase();
                // `assistant_count = _asst_counts.get(row.id, 0)`
                let assistant_count =
                    asst_counts.get(&row.id).and_then(Value::as_i64).unwrap_or(0);
                if THROWAWAY_NAMES.contains(&name.as_str())
                    || name.starts_with("chat:")
                    || THROWAWAY_NAMES.contains(&first_text.as_str())
                {
                    should_delete = true;
                    deleted_throwaway += 1;
                } else if msg_count == 1 && assistant_count == 0 {
                    should_delete = true;
                    deleted_throwaway += 1;
                } else if msg_count <= 2
                    && !first_text.is_empty()
                    && first_text.split_whitespace().count() <= 3
                    && first_text.chars().count() <= 40
                {
                    should_delete = true;
                    deleted_throwaway += 1;
                }
            }
            if should_delete {
                to_delete.push(row.id.clone());
            }
        }

        // `db.delete(row); session_manager.delete_session(row.id)` per deletion, then
        // `if deleted: db.commit()`. The Python deletes the session ROW only (not its
        // messages) inside this tx, but `session_manager.delete_session` (a second
        // connection) cascades the messages + evicts the cache. Reproduce the net
        // effect: delete messages + row in this tx and evict the cache.
        if !to_delete.is_empty() {
            let tx = conn.transaction().map_err(db_500)?;
            for id in &to_delete {
                tx.execute("DELETE FROM chat_messages WHERE session_id = ?1", [id])
                    .map_err(db_500)?;
                tx.execute("DELETE FROM sessions WHERE id = ?1", [id])
                    .map_err(db_500)?;
            }
            tx.commit().map_err(db_500)?;
            s.sessions.evict_cached_sessions(&to_delete);
            crate::pylog::info(&format!(
                "Auto-sort: deleted {deleted_empty} empty + {deleted_throwaway} throwaway sessions"
            ));
        }
    }

    // `if deleted_empty or deleted_throwaway: user_sessions = get_sessions_for_user(user)`
    if deleted_empty != 0 || deleted_throwaway != 0 {
        user_sessions = s.sessions.get_sessions_for_user(user.as_deref());
    }

    // ── Short-circuit when only the cleanup phase was requested. ──
    if q.skip_llm {
        return Ok(Json(json!({
            "status": "ok",
            "updated": 0,
            "folders": [],
            "deleted_empty": deleted_empty,
            "deleted_throwaway": deleted_throwaway,
            "unfiled_remaining": 0,
            "skipped_llm": true,
        }))
        .into_response());
    }

    // ── Phase 2: gather unfiled candidates (no folder), most-recent first. ──
    const TIDY_BATCH_SIZE: usize = 15;
    // Each candidate: (id, name). `updated_at` sort key — sessions in the manager
    // carry no `updated_at` field (the Session struct has none), so Python's
    // `getattr(s, "updated_at", None) or getattr(s, "created_at", None) or ""`
    // resolves to "" for every session (the attribute does not exist on the
    // in-memory Session). The sort is therefore stable over insertion order, exactly
    // as in Python (all keys equal -> Python's stable sort preserves order).
    let mut all_candidates: Vec<(String, String)> = Vec::new();
    for sess in user_sessions.values() {
        // `if s.archived or s.name == "Incognito": continue`
        if sess.archived || sess.name == "Incognito" {
            continue;
        }
        // `if folder_map.get(s.id): continue` — skip already-filed (Python truthiness).
        if folder_map
            .get(&sess.id)
            .map(|v| matches!(v, Value::String(s) if !s.is_empty()))
            .unwrap_or(false)
        {
            continue;
        }
        // `name = s.name or "(unnamed)"`
        let name = if sess.name.is_empty() {
            "(unnamed)".to_string()
        } else {
            sess.name.clone()
        };
        all_candidates.push((sess.id.clone(), name));
    }

    // `all_candidates.sort(key=lambda x: x.get("updated_at") or "", reverse=True)` —
    // all keys are "" (see above), so this is a stable no-op reorder.
    let unfiled_total = all_candidates.len();
    let session_list: Vec<(String, String)> =
        all_candidates.into_iter().take(TIDY_BATCH_SIZE).collect();

    // `if len(session_list) < 2:`
    if session_list.len() < 2 {
        if deleted_empty != 0 || deleted_throwaway != 0 {
            return Ok(Json(json!({
                "status": "ok",
                "updated": 0,
                "folders": [],
                "deleted_empty": deleted_empty,
                "deleted_throwaway": deleted_throwaway,
                "unfiled_remaining": unfiled_total,
            }))
            .into_response());
        }
        return Ok(Json(json!({
            "status": "skipped",
            "reason": "No unfiled sessions to sort",
        }))
        .into_response());
    }

    // ── Pick an endpoint — prefer admin-configured task endpoint, then the sort
    //    cascade (`resolve_task_endpoint(owner=user)` -> `_pick_endpoint_for_sort(owner=user)`). ──
    let (url, model, headers) = match pick_sort_endpoint(user.as_deref()) {
        Some(triple) => triple,
        // `if not url: raise HTTPException(503, "No available model endpoint for auto-sort")`
        None => {
            return Err(HttpException::new(
                503,
                "No available model endpoint for auto-sort",
            ))
        }
    };

    // `names_text = "\n".join(f'  "{s["id"][:8]}": "{s["name"]}"' for s in session_list)`
    let names_text: String = session_list
        .iter()
        .map(|(id, name)| format!("  \"{}\": \"{}\"", first8(id), name))
        .collect::<Vec<_>>()
        .join("\n");
    let prompt = format!(
        "You are a session organizer. Group these chat sessions into folders by topic.\n\n\
         Rules:\n\
         - Be aggressive about grouping — put EVERY session in a folder\n\
         - Use short folder names (2-4 words max)\n\
         - Use the 8-char ID prefixes exactly as given\n\
         - Output ONLY raw JSON, no markdown fences, no explanation\n\n\
         Required JSON format:\n\
         {{\"folders\": {{\"Folder Name\": [\"id_prefix1\", \"id_prefix2\"], \"Other Folder\": [\"id_prefix3\"]}}}}\n\n\
         Sessions (id_prefix: name):\n{{\n{names_text}\n}}"
    );

    // `raw = llm_call(url, model, [{"role":"user","content":prompt}], temperature=0.3,
    //   max_tokens=16384, headers=headers, timeout=120)` — async port.
    crate::pylog::info(&format!("Auto-sort: using model={model} at {url}"));
    let raw = match crate::src::llm_core::llm_call_async(
        &url,
        &model,
        vec![json!({ "role": "user", "content": prompt })],
        0.3,
        16384,
        headers,
        120,
    )
    .await
    {
        Ok(r) => r,
        // `except Exception as e: raise HTTPException(502, f"Auto-sort failed: {str(e)}")`
        Err(e) => {
            crate::pylog::error(&format!("Auto-sort LLM call failed: {e}"));
            return Err(HttpException::new(502, format!("Auto-sort failed: {e}")));
        }
    };
    crate::pylog::info(&format!(
        "Auto-sort raw response ({} chars): {}",
        raw.len(),
        first_chars(&raw, 300)
    ));

    // ── Parse the JSON (strip <think>, lenient trailing-comma, fence, brace scan). ──
    let result = match parse_autosort_json(&raw) {
        Some(v) => v,
        None => {
            crate::pylog::error(&format!(
                "Auto-sort: could not parse JSON from: {}",
                first_chars(&raw, 500)
            ));
            return Err(HttpException::new(
                502,
                "AI returned invalid JSON for auto-sort — the model may not follow JSON instructions; try a different utility model in Settings.",
            ));
        }
    };

    // `folders = result.get("folders", {}); if not folders: skipped`
    let folders = result
        .get("folders")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    if folders.is_empty() {
        return Ok(Json(json!({ "status": "skipped", "reason": "AI found no groupings" }))
            .into_response());
    }

    // `id_prefix_map = {s["id"][:8]: s["id"] for s in session_list}`
    let mut id_prefix_map: IndexMap<String, String> = IndexMap::new();
    for (id, _) in &session_list {
        id_prefix_map.insert(first8(id), id.clone());
    }
    // `assignments = {}` — match by full ID or prefix (exact / trimmed / fuzzy).
    let mut assignments: IndexMap<String, String> = IndexMap::new();
    for (folder_name, ids) in &folders {
        let ids_arr = match ids.as_array() {
            Some(a) => a,
            None => continue,
        };
        for sid_or_prefix_v in ids_arr {
            let sid_or_prefix = match sid_or_prefix_v.as_str() {
                Some(s) => s,
                None => continue,
            };
            // `if sid_or_prefix in id_prefix_map.values(): full_id = sid_or_prefix`
            let mut full_id: Option<String> = None;
            if id_prefix_map.values().any(|v| v == sid_or_prefix) {
                full_id = Some(sid_or_prefix.to_string());
            } else {
                // `prefix = sid_or_prefix.rstrip(".").rstrip(" ")`
                let prefix = sid_or_prefix.trim_end_matches('.').trim_end_matches(' ');
                if let Some(fid) = id_prefix_map.get(prefix) {
                    full_id = Some(fid.clone());
                } else {
                    // Fuzzy: `fid.startswith(prefix) or prefix.startswith(p)`.
                    for (p, fid) in &id_prefix_map {
                        if fid.starts_with(prefix) || prefix.starts_with(p.as_str()) {
                            full_id = Some(fid.clone());
                            break;
                        }
                    }
                }
            }
            if let Some(fid) = full_id {
                assignments.insert(fid, folder_name.clone());
            }
        }
    }

    // ── Apply folder assignments. ──
    let updated: i64 = {
        let conn = session_local()?;
        // OWNER-SCOPED: `query(DbSession).filter(id == sid, DbSession.owner == user)` —
        // resolve + UPDATE only the caller's own row (strict equality; `owner IS NULL`
        // for a None/empty user). Without it a fuzzy-matched id prefix could relabel
        // another user's session.
        let owner = user.as_deref().filter(|u| !u.is_empty());
        let apply = (|| -> rusqlite::Result<i64> {
            let mut n = 0i64;
            for (sid, folder_name) in &assignments {
                // `db_session = query(...).filter(id == sid, owner == user).first()`
                let exists: bool = match owner {
                    Some(u) => conn
                        .query_row(
                            "SELECT 1 FROM sessions WHERE id = ?1 AND owner = ?2",
                            rusqlite::params![sid, u],
                            |_| Ok(()),
                        )
                        .optional()?
                        .is_some(),
                    None => conn
                        .query_row(
                            "SELECT 1 FROM sessions WHERE id = ?1 AND owner IS NULL",
                            [sid],
                            |_| Ok(()),
                        )
                        .optional()?
                        .is_some(),
                };
                if exists {
                    let now = crate::pydatetime::utcnow_naive_iso();
                    match owner {
                        Some(u) => conn.execute(
                            "UPDATE sessions SET folder = ?1, updated_at = ?2 \
                             WHERE id = ?3 AND owner = ?4",
                            rusqlite::params![folder_name, now, sid, u],
                        )?,
                        None => conn.execute(
                            "UPDATE sessions SET folder = ?1, updated_at = ?2 \
                             WHERE id = ?3 AND owner IS NULL",
                            rusqlite::params![folder_name, now, sid],
                        )?,
                    };
                    n += 1;
                }
            }
            Ok(n)
        })();
        match apply {
            Ok(n) => n,
            // `except Exception: db.rollback(); raise HTTPException(500, ...)`
            Err(e) => {
                crate::pylog::error(&format!("Auto-sort DB update failed: {e}"));
                return Err(HttpException::new(500, "Failed to apply folder assignments"));
            }
        }
    };

    // `unfiled_remaining_after = max(0, unfiled_total - updated)`
    let unfiled_remaining_after = (unfiled_total as i64 - updated).max(0);
    Ok(Json(json!({
        "status": "ok",
        "folders": folders.keys().cloned().collect::<Vec<_>>(),
        "updated": updated,
        "deleted_empty": deleted_empty,
        "deleted_throwaway": deleted_throwaway,
        "unfiled_remaining": unfiled_remaining_after,
    }))
    .into_response())
}

// ---------------------------------------------------------------------------
// GET /api/session/:session_id/context_info
// ---------------------------------------------------------------------------

/// `GET /api/session/{session_id}/context_info` — the real context length for a
/// session's model. Ownership-gated; `get_session` (the Python's `if not session:`
/// guard is dead — `get_session` raises rather than returning None — so the 404 arm
/// is unreachable, mirrored here by treating a KeyError as a missing session). Empty
/// endpoint/model -> `{"context_length": None}`; else
/// `{"context_length": <ctx>, "model": <model>}`.
async fn get_context_info(
    State(s): State<AppState>,
    tok: Option<Extension<ApiToken>>,
    user: Option<Extension<CurrentUser>>,
    Path(session_id): Path<String>,
) -> Result<Response, HttpException> {
    // `_verify_session_owner(request, session_id)` — effective-user owner gate.
    let user = effective_user(&tok, &user);
    auth_adapter::verify_session_owner(&s, user.as_deref(), &session_id)?;
    // `session = session_manager.get_session(session_id)` then `if not session: 404`.
    // `get_session` raises KeyError on a miss (the `if not session` guard is dead in
    // Python because the manager never returns a falsy session); the ownership guard
    // above already 404s a missing/foreign session, so reaching here means it exists.
    let session = match s.sessions.get_session(&session_id) {
        Ok(se) => se,
        Err(_) => return Err(HttpException::new(404, "Session not found")),
    };
    // `if not session.endpoint_url or not session.model: return {"context_length": None}`
    if session.endpoint_url.is_empty() || session.model.is_empty() {
        return Ok(Json(json!({ "context_length": Value::Null })).into_response());
    }
    // `try: ctx = get_context_length(...); return {"context_length": ctx, "model": ...}
    //  except: return {"context_length": None}` — `get_context_length` is infallible
    //  here (returns an i64), so the except arm is not reachable on this path.
    let ctx = crate::src::model_context::get_context_length(&session.endpoint_url, &session.model);
    Ok(Json(json!({ "context_length": ctx, "model": session.model })).into_response())
}

// ===========================================================================
// Shared helpers
// ===========================================================================

/// `user = get_current_user(request)` — the resolved username or `None` (the
/// load-bearing auth-disabled / single-user case).
fn current_user(user: &Option<Extension<CurrentUser>>) -> Option<String> {
    user.as_ref().map(|Extension(CurrentUser(u))| u.clone())
}

/// `effective_user(request)` (src/auth_helpers.py) — the real human behind the
/// request for ownership/attribution.
///
/// ```python
/// def effective_user(request):
///     if getattr(request.state, "api_token", False):
///         owner = getattr(request.state, "api_token_owner", None)
///         if owner:
///             return owner
///     return get_current_user(request)
/// ```
///
/// A validated Bearer token (`ApiToken.present`) with a non-empty `owner` resolves
/// to that owner so a paired API client sees/creates the SAME data as the owner's
/// browser UI; otherwise (cookie session, or a token with no owner) it falls back to
/// `get_current_user`. The `ApiToken` extension is absent on cookie/internal paths
/// (`present = false`, matching Python's `getattr(..., False)`).
fn effective_user(
    tok: &Option<Extension<ApiToken>>,
    user: &Option<Extension<CurrentUser>>,
) -> Option<String> {
    if let Some(Extension(t)) = tok {
        // `if getattr(request.state, "api_token", False):`
        if t.present {
            // `owner = getattr(request.state, "api_token_owner", None); if owner: return`
            if let Some(owner) = t.owner.as_deref().filter(|o| !o.is_empty()) {
                return Some(owner.to_string());
            }
        }
    }
    // `return get_current_user(request)`
    current_user(user)
}

/// `_current_user_is_admin(request, user)` (session_routes.py) — `False` for no user,
/// else `bool(auth_manager.is_admin(user))`. Reads `state.auth` (the Rust
/// `auth_manager`); a missing/uncallable `is_admin` in Python returns `False`, which
/// here is the empty-user / not-an-admin path.
fn current_user_is_admin(state: &AppState, user: Option<&str>) -> bool {
    match user.filter(|u| !u.is_empty()) {
        Some(u) => state.auth.is_admin(u),
        None => false,
    }
}

/// `_reject_raw_endpoint_url_for_non_admin(request, user, endpoint_id, endpoint_url)`
/// (session_routes.py) — require registered endpoints for signed-in non-admin session
/// changes.
///
/// ```python
/// if endpoint_id and endpoint_id.strip():
///     return
/// if not endpoint_url:
///     return
/// if user and not _current_user_is_admin(request, user):
///     raise HTTPException(403, "Choose a registered model endpoint")
/// ```
///
/// Raw URLs make the server dial whatever host the request supplies; for non-admin
/// signed-in users, require a saved endpoint row so owner scoping + endpoint
/// validation have already happened.
fn reject_raw_endpoint_url_for_non_admin(
    state: &AppState,
    user: Option<&str>,
    endpoint_id: &str,
    endpoint_url: &str,
) -> Result<(), HttpException> {
    // `if endpoint_id and endpoint_id.strip(): return`
    if !endpoint_id.trim().is_empty() {
        return Ok(());
    }
    // `if not endpoint_url: return`
    if endpoint_url.is_empty() {
        return Ok(());
    }
    // `if user and not _current_user_is_admin(request, user): raise HTTPException(403, ...)`
    if user.filter(|u| !u.is_empty()).is_some() && !current_user_is_admin(state, user) {
        return Err(HttpException::new(403, "Choose a registered model endpoint"));
    }
    Ok(())
}

/// `_verify_session_owner(request, session_id, session_manager)` (session_routes.py)
/// WITH the in-memory ghost-session fallback — the variant the bulk-delete / delete
/// paths pass the manager to.
///
/// First the shared owner check (a direct DB read in
/// [`auth_adapter::verify_session_owner`]). When that 404s **because there is no DB
/// row**, fall back to the in-memory `session_manager.sessions[session_id]`: if the
/// caller owns that ghost (its `owner == user`), allow the op so a never-persisted /
/// out-of-band-removed session can still be managed and deleted (issue #1044). A 403
/// (no authenticated user) is never softened. A 404 that is NOT a missing-row case
/// (i.e. the row exists but is owned by someone else) is preserved.
fn verify_session_owner_with_ghost(
    state: &AppState,
    user: Option<&str>,
    session_id: &str,
) -> Result<(), HttpException> {
    // `user = effective_user(request); if not user: raise HTTPException(403, ...)`
    // (the shared helper performs this same 403 check first).
    match auth_adapter::verify_session_owner(state, user, session_id) {
        Ok(()) => Ok(()),
        Err(e) => {
            // A 403 (no user) or a 500 (DB failure) is propagated unchanged.
            if e.status_code != 404 {
                return Err(e);
            }
            // `if row is not None:` — the shared check already 404'd a foreign DB row.
            // Only fall back when there is NO DB row at all; a present-but-foreign row
            // must keep its 404, so re-confirm the row is genuinely absent.
            if session_row_exists(session_id) {
                return Err(e);
            }
            // `if session_manager is not None: ghost = sessions.get(session_id);
            //   if ghost is not None and ghost.owner == user: return`
            let user = match user.filter(|u| !u.is_empty()) {
                Some(u) => u,
                None => return Err(e),
            };
            if let Some(ghost) = state.sessions.peek_session(session_id) {
                if ghost.owner.as_deref() == Some(user) {
                    return Ok(());
                }
            }
            // `raise HTTPException(404, f"Session {session_id} not found")`
            Err(e)
        }
    }
}

/// Best-effort check whether a `sessions` row exists by id (the `row is not None`
/// branch of the ghost fallback). A DB error is treated as "exists" so we never
/// soften a 404 on an unreadable DB.
fn session_row_exists(session_id: &str) -> bool {
    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(_) => return true,
    };
    conn.query_row("SELECT 1 FROM sessions WHERE id = ?1", [session_id], |_| Ok(()))
        .optional()
        .map(|o| o.is_some())
        .unwrap_or(true)
}

/// `_sanitize_export_filename(name)` (session_routes.py) — a conservative filename
/// safe for `Content-Disposition`.
///
/// ```python
/// name = name if isinstance(name, str) else ""
/// name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
/// return name[:128]
/// ```
///
/// Replaces every char outside `[A-Za-z0-9._-]` with `_` and truncates to 128 chars
/// (by code point, matching Python's slice). Closes a header-injection / path-traversal
/// vector where a caller-supplied `filename` flowed unescaped into the response header.
fn sanitize_export_filename(name: &str) -> String {
    name.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '.' || c == '_' || c == '-' {
                c
            } else {
                '_'
            }
        })
        .take(128)
        .collect()
}

/// `html.escape(s)` — escape `&`, `<`, `>`, `"`, `'` for safe HTML text/attribute
/// interpolation (Python's `html.escape` default `quote=True`). Closes the stored-XSS
/// vector where `session.name` flowed unescaped into the HTML export's `<title>` /
/// `<h1>`.
fn html_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#x27;"),
            _ => out.push(c),
        }
    }
    out
}

/// `_persist_session_headers(session_id, headers)` (session_routes.py) — persist
/// endpoint auth headers for DB-backed session metadata.
///
/// ```python
/// db_session = query(DbSession).filter(id == session_id).first()
/// if db_session:
///     db_session.headers = headers or {}
///     db_session.updated_at = datetime.utcnow()
///     db.commit()
/// ```
///
/// Unlike `SessionManager::update_session_headers` (which writes the `headers` column
/// only), this also bumps `updated_at`, matching the Python persistor. In-memory
/// state is updated separately by the caller via `with_session_mut`.
fn persist_session_headers(session_id: &str, headers: &IndexMap<String, String>) {
    if let Ok(conn) = crate::core::database::session_local() {
        // `headers or {}` — already a (possibly empty) map here.
        let json_text = serde_json::to_string(headers).unwrap_or_else(|_| "{}".to_string());
        let now = crate::pydatetime::utcnow_naive_iso();
        let _ = conn.execute(
            "UPDATE sessions SET headers = ?1, updated_at = ?2 WHERE id = ?3",
            rusqlite::params![json_text, now, session_id],
        );
    }
}

/// The owner-scoped, `is_enabled = 1` model-endpoint lookup the create / rename paths
/// now use:
///
/// ```python
/// q = query(ModelEndpoint).filter(id == endpoint_id, is_enabled == True)
/// if user: q = owner_filter(q, ModelEndpoint, user)
/// ep = q.first()
/// ```
///
/// Returns `(base_url, decrypted_api_key)` for the enabled, owner-visible endpoint, or
/// `None` when no such row exists (the 400 "Model endpoint no longer exists" case).
/// `owner_filter` (truthy `user`) becomes the `(owner = ? OR owner IS NULL)` predicate
/// SQLAlchemy emits (the same shape `endpoint_resolver::resolve_endpoint_row` uses);
/// an empty user is the single-user unscoped path. `EncryptedText` decrypts on read,
/// so the returned key is PLAINTEXT.
fn model_endpoint_enabled_owner_scoped(
    id: &str,
    user: Option<&str>,
) -> Result<Option<(String, Option<String>)>, HttpException> {
    let conn = session_local()?;
    let user = user.filter(|u| !u.is_empty());
    let map_row = |r: &rusqlite::Row| -> rusqlite::Result<(String, Option<String>)> {
        Ok((r.get::<_, String>(0)?, r.get::<_, Option<String>>(1)?))
    };
    let row: Option<(String, Option<String>)> = match user {
        Some(u) => conn
            .query_row(
                "SELECT base_url, api_key FROM model_endpoints \
                 WHERE id = ?1 AND is_enabled = 1 AND (owner = ?2 OR owner IS NULL)",
                rusqlite::params![id, u],
                map_row,
            )
            .optional()
            .map_err(db_500)?,
        None => conn
            .query_row(
                "SELECT base_url, api_key FROM model_endpoints \
                 WHERE id = ?1 AND is_enabled = 1",
                rusqlite::params![id],
                map_row,
            )
            .optional()
            .map_err(db_500)?,
    };
    Ok(row.map(|(base, enc)| {
        let key = enc
            .filter(|k| !k.is_empty())
            .map(|k| crate::src::secret_storage::decrypt(&k));
        (base, key)
    }))
}

/// Collect all `multipart/form-data` text fields into a map (the FastAPI `Form(...)`
/// surface). Mirrors `web/mod.rs::parse_form`.
async fn parse_form(mut mp: Multipart) -> std::collections::HashMap<String, String> {
    let mut out = std::collections::HashMap::new();
    while let Ok(Some(field)) = mp.next_field().await {
        let name = field.name().unwrap_or("").to_string();
        if name.is_empty() {
            continue;
        }
        if let Ok(text) = field.text().await {
            out.insert(name, text);
        }
    }
    out
}

/// `Form("")` — a string field defaulting to `""` when absent.
fn form_get(f: &std::collections::HashMap<String, String>, key: &str) -> String {
    f.get(key).cloned().unwrap_or_default()
}

/// `Form("default")` — a string field defaulting to `default` when absent.
fn form_get_with_default(
    f: &std::collections::HashMap<String, String>,
    key: &str,
    default: &str,
) -> String {
    f.get(key).cloned().unwrap_or_else(|| default.to_string())
}

/// `Form(None)` — `Some(value)` only when the field is present (even if empty), else
/// `None`. Matches Python's `x is not None` present-field tests.
fn form_get_opt(f: &std::collections::HashMap<String, String>, key: &str) -> Option<String> {
    f.get(key).cloned()
}

/// `str(rag).lower() == "true" if rag else False` — Python truthiness: an absent /
/// empty `rag` is falsy and yields `False` WITHOUT the `.lower()=="true"` test.
fn rag_truthy(rag: Option<&str>) -> bool {
    match rag {
        Some(r) if !r.is_empty() => r.to_lowercase() == "true",
        _ => false,
    }
}

/// Parse a pydantic-style form bool (`important: bool`). FastAPI accepts
/// `true/false/1/0/on/off/yes/no` (case-insensitive); anything else is a 422.
fn parse_form_bool(v: &str) -> Result<bool, HttpException> {
    match v.trim().to_lowercase().as_str() {
        "true" | "1" | "on" | "yes" => Ok(true),
        "false" | "0" | "off" | "no" => Ok(false),
        _ => Err(HttpException::new(422, "Unprocessable Entity")),
    }
}

/// `os.path.basename(value.rstrip("/"))` — strip a trailing slash then take the last
/// path segment.
fn basename(value: &str) -> String {
    let trimmed = value.trim_end_matches('/');
    trimmed.rsplit('/').next().unwrap_or(trimmed).to_string()
}

/// `filename or f"conversation_{safe_name}_{timestamp}.{ext}"` — Python truthiness:
/// a present non-empty `filename` wins, else the generated name.
fn filename_or(filename: &str, safe_name: &str, timestamp: &str, ext: &str) -> String {
    if filename.is_empty() {
        format!("conversation_{safe_name}_{timestamp}.{ext}")
    } else {
        filename.to_string()
    }
}

/// `dt.isoformat() if dt else None` — render a stored SQLite datetime with Python's
/// `isoformat` (plain, no `Z`), or `null` when absent/empty.
fn iso_or_null(stored: Option<&str>) -> Value {
    match stored.filter(|s| !s.is_empty()) {
        Some(s) => json!(crate::pydatetime::to_isoformat(s)),
        None => Value::Null,
    }
}

/// `Optional[str]` -> JSON string-or-null (the folder/mode map values).
fn opt_str_to_json(v: Option<String>) -> Value {
    v.map(Value::String).unwrap_or(Value::Null)
}

/// `value[:8]` — the first 8 chars of an id (Python slices by code point).
fn first8(id: &str) -> String {
    id.chars().take(8).collect()
}

/// `text[:n]` — first `n` chars (Python slices by code point).
fn first_chars(text: &str, n: usize) -> String {
    text.chars().take(n).collect()
}

// ---------------------------------------------------------------------------
// Raw-SQL helpers (module-private; single-writer)
// ---------------------------------------------------------------------------

/// Open a DB connection (`SessionLocal()`), mapping a failure to a 500.
fn session_local() -> Result<rusqlite::Connection, HttpException> {
    crate::core::database::session_local().map_err(db_500)
}

/// Open a raw DB connection for the best-effort transactional paths (the purge /
/// bulk-delete / auto-sort), surfacing the `rusqlite::Error` to the caller.
fn session_local_raw() -> rusqlite::Result<rusqlite::Connection> {
    crate::core::database::session_local()
}

/// Map a `rusqlite::Error` to a 500 `HttpException` (an unhandled handler exception).
fn db_500(e: rusqlite::Error) -> HttpException {
    crate::pylog::error(&format!("session_routes DB error: {e}"));
    HttpException::new(500, "Internal Server Error")
}

// The old unscoped `model_endpoint_api_key_by_id` / `model_endpoint_auth_by_id`
// lookups were removed: both create and rename now use
// `model_endpoint_enabled_owner_scoped` (the `is_enabled = 1` + owner-filter +
// build_chat_url/normalize_base parity path) instead of a raw id read.

// ---------------------------------------------------------------------------
// Model-list probing (the create_session validation path)
// ---------------------------------------------------------------------------

/// `list_model_ids(base_chat_url, timeout, headers)` (src/llm_core.py:746) — list a
/// remote endpoint's model IDs.
///
/// Mirrors the Python network helper (the Rust `llm_core::list_model_ids` is a
/// DIFFERENT pure model-id splitter, so this is written inline): Anthropic providers
/// short-circuit to [`crate::src::llm_core::ANTHROPIC_MODELS`] (no `/models`
/// discovery); otherwise it GETs `<base>.replace("/chat/completions", "/models")`
/// with the optional auth `headers` dict (`h = {}; if headers: h.update(headers)`)
/// and returns `[m["id"] for m in r.json()["data"] if m["id"]]`. Any failure
/// (unreachable / non-2xx / parse) -> `[]` (the Python `except Exception: return []`).
///
/// The `headers` argument is now the FULL validation-header dict the create path
/// builds via `build_headers(effective_api_key, base)` (so e.g. Anthropic's
/// `x-api-key` / OpenRouter's referer headers reach the probe), not a bare bearer key.
async fn list_model_ids_with_headers(
    base_chat_url: &str,
    headers: Option<&IndexMap<String, String>>,
) -> Vec<String> {
    // `if _detect_provider(base_chat_url) == "anthropic": return list(ANTHROPIC_MODELS)`
    if crate::src::llm_core::_detect_provider(base_chat_url) == "anthropic" {
        return crate::src::llm_core::ANTHROPIC_MODELS
            .iter()
            .map(|m| m.to_string())
            .collect();
    }
    // `r = httpx.get(base.replace("/chat/completions", "/models"), headers=h, timeout=timeout)`
    let url = base_chat_url.replace("/chat/completions", "/models");
    let client = match reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(20))
        .build()
    {
        Ok(c) => c,
        Err(_) => return vec![],
    };
    let mut req = client.get(&url);
    // `h = {}; if headers: h.update(headers)` — apply every supplied header.
    if let Some(h) = headers {
        for (k, v) in h {
            req = req.header(k, v);
        }
    }
    let resp = match req.send().await {
        Ok(r) => r,
        Err(_) => return vec![],
    };
    // `r.raise_for_status()` — a non-2xx raises -> `except -> []`.
    if !resp.status().is_success() {
        return vec![];
    }
    let j: Value = match resp.json().await {
        Ok(v) => v,
        Err(_) => return vec![],
    };
    // `[m.get("id") for m in (r.json().get("data") or []) if m.get("id")]`
    j.get("data")
        .and_then(Value::as_array)
        .map(|arr| {
            arr.iter()
                .filter_map(|m| m.get("id").and_then(Value::as_str).map(String::from))
                .collect()
        })
        .unwrap_or_default()
}

// ---------------------------------------------------------------------------
// Auto-sort endpoint resolution + JSON parsing
// ---------------------------------------------------------------------------

/// The auto-sort endpoint cascade: `resolve_task_endpoint(owner=user)` (admin task
/// endpoint) first, then `_pick_endpoint_for_sort(owner=user)` (utility -> task ->
/// default).
///
/// Mirrors:
/// ```python
/// url, model, headers = resolve_task_endpoint(owner=user)
/// if not url: url, model, headers = _pick_endpoint_for_sort(owner=user)
/// if not url: raise HTTPException(503, ...)
/// ```
/// Returns `(chat_url, model, headers)` or `None` when nothing resolves (the 503).
///
/// `owner` is threaded into `resolve_task_endpoint(owner=user)` (which now reads the
/// per-user default/utility setting overrides) AND the utility/default
/// `resolve_endpoint_triple` calls (which owner-scope their `model_endpoints`
/// lookup), matching the Python `owner=user`.
fn pick_sort_endpoint(owner: Option<&str>) -> Option<(String, String, IndexMap<String, String>)> {
    // `resolve_task_endpoint(owner=user)` — no endpoint fallbacks (first 3 args None).
    let (url, model, headers) =
        crate::src::task_endpoint::resolve_task_endpoint(None, None, None, owner);
    if let Some(url) = url.filter(|u| !u.is_empty()) {
        // `if not url:` is false -> use the task-endpoint result. The model may be
        // None (no `task_model` and no fallback); the caller's `llm_call` would send
        // `model=None`. Mirror Python: keep whatever model resolved (empty allowed).
        let model = model.unwrap_or_default();
        let headers = map_to_indexmap(headers);
        return Some((url, model, headers));
    }

    // `_pick_endpoint_for_sort(owner=user)` — utility -> task -> default.
    pick_endpoint_for_sort(owner)
}

/// `_pick_endpoint_for_sort(owner=None)` (session_routes.py:109) — utility endpoint,
/// then the task endpoint, then default. Returns `None` when none resolves.
///
/// `owner` is threaded into the `resolve_endpoint("utility"/"default", owner=owner)`
/// calls (owner-scoped `model_endpoints` lookup). The intermediate
/// `resolve_task_endpoint` is admin-settings-only in the Rust port (see
/// `pick_sort_endpoint`).
fn pick_endpoint_for_sort(
    owner: Option<&str>,
) -> Option<(String, String, IndexMap<String, String>)> {
    // `url, model, headers = resolve_endpoint("utility", owner=owner); if url and model: return`
    if let Some(triple) =
        crate::src::endpoint_resolver::resolve_endpoint_triple("utility", owner)
    {
        return Some(triple);
    }
    // `try: url, model, headers = resolve_task_endpoint(owner=owner); if url and model:
    //  return except: pass`
    let (url, model, headers) =
        crate::src::task_endpoint::resolve_task_endpoint(None, None, None, owner);
    if let (Some(url), Some(model)) = (
        url.filter(|u| !u.is_empty()),
        model.filter(|m| !m.is_empty()),
    ) {
        return Some((url, model, map_to_indexmap(headers)));
    }
    // `url, model, headers = resolve_endpoint("default", owner=owner); if url and model: return`
    if let Some(triple) =
        crate::src::endpoint_resolver::resolve_endpoint_triple("default", owner)
    {
        return Some(triple);
    }
    None
}

/// `Map<String, Value>` (the task-endpoint header dict) -> `IndexMap<String, String>`
/// (what `llm_call_async` takes). Non-string values are stringified (HTTP headers are
/// str->str, so this is contractually safe).
fn map_to_indexmap(headers: Option<Map<String, Value>>) -> IndexMap<String, String> {
    let mut out = IndexMap::new();
    if let Some(m) = headers {
        for (k, v) in m {
            let val = match v {
                Value::String(s) => s,
                other => other.to_string(),
            };
            out.insert(k, val);
        }
    }
    out
}

/// Parse the auto-sort LLM response into a JSON object, reproducing the Python's
/// tolerant extraction:
/// 1. strip `<think>...</think>` (reasoning models),
/// 2. lenient parse (retry with trailing commas stripped),
/// 3. markdown code-fence content,
/// 4. first-`{` .. last-`}` brace block.
///
/// Returns `None` when none of the strategies yields valid JSON.
fn parse_autosort_json(raw: &str) -> Option<Value> {
    // `text = raw.strip()`
    let text = raw.trim();
    // `text = re.sub(r'<think(?:ing)?>[\s\S]*?</think(?:ing)?>', '', text, flags=re.I).strip()`
    let text = strip_think_blocks(text);
    let text = text.trim();

    // `result = _loads_lenient(text)`
    if let Some(v) = loads_lenient(text) {
        return Some(v);
    }
    // Markdown code fence: `re.search(r'```(?:json)?\s*\n?([\s\S]*?)```', text)`
    if let Some(inner) = extract_fence(text) {
        if let Some(v) = loads_lenient(inner.trim()) {
            return Some(v);
        }
    }
    // First `{` .. last `}` block.
    if let (Some(start), Some(end)) = (text.find('{'), text.rfind('}')) {
        if end > start {
            if let Some(v) = loads_lenient(&text[start..=end]) {
                return Some(v);
            }
        }
    }
    None
}

/// `_loads_lenient(s)` — parse JSON, retrying once with trailing commas stripped.
/// `None` for empty input or when both attempts fail.
fn loads_lenient(s: &str) -> Option<Value> {
    if s.is_empty() {
        return None;
    }
    if let Ok(v) = serde_json::from_str::<Value>(s) {
        return Some(v);
    }
    // `re.sub(r',(\s*[}\]])', r'\1', s)` — strip a comma before a closing `}`/`]`.
    let stripped = strip_trailing_commas(s);
    serde_json::from_str::<Value>(&stripped).ok()
}

/// Remove `<think>...</think>` / `<thinking>...</thinking>` blocks (case-insensitive,
/// non-greedy), mirroring the Python regex.
fn strip_think_blocks(text: &str) -> String {
    let lower = text.to_lowercase();
    let mut out = String::new();
    let mut cursor = 0usize;
    loop {
        // Find the next opening tag (<think> or <thinking>) from `cursor`.
        let open_think = lower[cursor..].find("<think>").map(|i| (cursor + i, "</think>", 7));
        let open_thinking = lower[cursor..]
            .find("<thinking>")
            .map(|i| (cursor + i, "</thinking>", 10));
        let open = match (open_think, open_thinking) {
            (Some(a), Some(b)) => {
                if a.0 <= b.0 {
                    a
                } else {
                    b
                }
            }
            (Some(a), None) => a,
            (None, Some(b)) => b,
            (None, None) => {
                out.push_str(&text[cursor..]);
                break;
            }
        };
        let (open_idx, close_tag, open_len) = open;
        // Keep everything up to the opening tag.
        out.push_str(&text[cursor..open_idx]);
        // Find the matching close tag.
        match lower[open_idx + open_len..].find(close_tag) {
            Some(rel) => {
                let close_idx = open_idx + open_len + rel + close_tag.len();
                cursor = close_idx;
            }
            None => {
                // No close tag — drop the rest (matches non-greedy `.*?` finding
                // nothing, so the unmatched open tail stays; but Python's `re.sub`
                // would leave an unmatched `<think>` in place). Preserve the unmatched
                // open tag verbatim to stay faithful.
                out.push_str(&text[open_idx..]);
                break;
            }
        }
    }
    out
}

/// `re.search(r'```(?:json)?\s*\n?([\s\S]*?)```', text)` — the content between the
/// first pair of triple-backtick fences (optionally tagged `json`).
fn extract_fence(text: &str) -> Option<String> {
    let start = text.find("```")?;
    let after = &text[start + 3..];
    // Optional `json` tag + whitespace + optional newline.
    let after = after.strip_prefix("json").unwrap_or(after);
    let after = after.trim_start_matches([' ', '\t']);
    let after = after.strip_prefix('\n').unwrap_or(after);
    let end = after.find("```")?;
    Some(after[..end].to_string())
}

/// `re.sub(r',(\s*[}\]])', r'\1', s)` — strip a comma that immediately precedes a
/// closing `}` / `]` (allowing whitespace between).
fn strip_trailing_commas(s: &str) -> String {
    let bytes: Vec<char> = s.chars().collect();
    let mut out = String::with_capacity(s.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == ',' {
            // Look ahead past whitespace for a closing brace/bracket.
            let mut j = i + 1;
            while j < bytes.len() && bytes[j].is_whitespace() {
                j += 1;
            }
            if j < bytes.len() && (bytes[j] == '}' || bytes[j] == ']') {
                // Drop the comma; keep the whitespace + closing char by continuing.
                i += 1;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn rag_truthy_python_semantics() {
        assert!(rag_truthy(Some("true")));
        assert!(rag_truthy(Some("True")));
        assert!(!rag_truthy(Some("false")));
        assert!(!rag_truthy(Some("yes"))); // only "true" is true
        assert!(!rag_truthy(Some("")));
        assert!(!rag_truthy(None));
    }

    #[test]
    fn basename_strips_trailing_slash_and_path() {
        assert_eq!(basename("foo/bar"), "bar");
        assert_eq!(basename("foo/bar/"), "bar");
        assert_eq!(basename("model"), "model");
        assert_eq!(basename("org/Model-7B"), "Model-7B");
    }

    #[test]
    fn filename_or_truthiness() {
        assert_eq!(filename_or("", "name", "ts", "md"), "conversation_name_ts.md");
        assert_eq!(filename_or("custom.json", "name", "ts", "json"), "custom.json");
    }

    #[test]
    fn first8_and_first_chars_code_points() {
        assert_eq!(first8("0123456789abcdef"), "01234567");
        let emoji: String = "🙂".repeat(20);
        assert_eq!(first_chars(&emoji, 5).chars().count(), 5);
    }

    #[test]
    fn loads_lenient_handles_trailing_commas() {
        assert_eq!(loads_lenient(r#"{"a": 1}"#), Some(json!({"a": 1})));
        assert_eq!(
            loads_lenient(r#"{"folders": {"X": ["a", "b",],}}"#),
            Some(json!({"folders": {"X": ["a", "b"]}}))
        );
        assert_eq!(loads_lenient(""), None);
        assert_eq!(loads_lenient("not json"), None);
    }

    #[test]
    fn strip_think_blocks_removes_reasoning() {
        assert_eq!(
            strip_think_blocks("<think>reasoning here</think>{\"x\":1}"),
            "{\"x\":1}"
        );
        assert_eq!(
            strip_think_blocks("<thinking>a</thinking>before<think>b</think>after"),
            "beforeafter"
        );
        // no think tags -> unchanged.
        assert_eq!(strip_think_blocks("plain text"), "plain text");
    }

    #[test]
    fn extract_fence_pulls_inner_json() {
        assert_eq!(
            extract_fence("```json\n{\"a\":1}\n```").as_deref(),
            Some("{\"a\":1}\n")
        );
        assert_eq!(
            extract_fence("text ```\n{\"b\":2}```").as_deref(),
            Some("{\"b\":2}")
        );
        assert_eq!(extract_fence("no fence"), None);
    }

    #[test]
    fn parse_autosort_json_strategies() {
        // direct.
        assert_eq!(
            parse_autosort_json(r#"{"folders": {"Work": ["abc"]}}"#),
            Some(json!({"folders": {"Work": ["abc"]}}))
        );
        // think block + fenced.
        assert_eq!(
            parse_autosort_json("<think>hmm</think>```json\n{\"folders\":{}}\n```"),
            Some(json!({"folders": {}}))
        );
        // leading prose + brace scan.
        assert_eq!(
            parse_autosort_json("Here you go: {\"folders\": {\"A\": [\"x\"]}} done"),
            Some(json!({"folders": {"A": ["x"]}}))
        );
        // unparseable.
        assert_eq!(parse_autosort_json("the model refused"), None);
    }

    #[test]
    fn strip_trailing_commas_only_before_close() {
        assert_eq!(strip_trailing_commas(r#"[1, 2,]"#), r#"[1, 2]"#);
        assert_eq!(strip_trailing_commas(r#"{"a": 1,}"#), r#"{"a": 1}"#);
        // a comma between elements is preserved.
        assert_eq!(strip_trailing_commas(r#"[1, 2, 3]"#), r#"[1, 2, 3]"#);
    }

    #[test]
    fn parse_form_bool_accepts_pydantic_forms() {
        assert!(parse_form_bool("true").unwrap());
        assert!(!parse_form_bool("False").unwrap());
        assert!(parse_form_bool("1").unwrap());
        assert!(!parse_form_bool("0").unwrap());
        assert!(parse_form_bool("maybe").is_err());
    }

    #[test]
    fn iso_or_null_renders_or_nulls() {
        assert_eq!(
            iso_or_null(Some("2026-06-01 12:30:00")),
            json!("2026-06-01T12:30:00")
        );
        assert_eq!(iso_or_null(None), Value::Null);
        assert_eq!(iso_or_null(Some("")), Value::Null);
    }

    // ── SECURITY: export filename sanitization (_sanitize_export_filename) ──
    #[test]
    fn sanitize_export_filename_scrubs_and_truncates() {
        // Plain allowed chars pass through unchanged.
        assert_eq!(sanitize_export_filename("chat_2026-06-01.md"), "chat_2026-06-01.md");
        // Path separators / spaces / quotes / CRLF -> "_".
        assert_eq!(
            sanitize_export_filename("../../etc/passwd"),
            ".._.._etc_passwd"
        );
        assert_eq!(
            sanitize_export_filename("a b\r\nContent-Disposition: x"),
            "a_b__Content-Disposition__x"
        );
        // Non-ASCII -> "_" (only [A-Za-z0-9._-] survive).
        assert_eq!(sanitize_export_filename("résumé.txt"), "r_sum_.txt");
        // Truncated to 128 chars.
        let long: String = "a".repeat(200);
        assert_eq!(sanitize_export_filename(&long).len(), 128);
    }

    // ── SECURITY: HTML export name escaping (html.escape) ──
    #[test]
    fn html_escape_neutralizes_markup() {
        assert_eq!(
            html_escape("<script>alert(1)</script>"),
            "&lt;script&gt;alert(1)&lt;/script&gt;"
        );
        assert_eq!(html_escape("a & b"), "a &amp; b");
        assert_eq!(html_escape("\"quoted' & <tag>"), "&quot;quoted&#x27; &amp; &lt;tag&gt;");
        // No markup -> unchanged.
        assert_eq!(html_escape("Plain Title 123"), "Plain Title 123");
    }

    // ── effective_user: token-owner attribution vs cookie fallback ──
    #[test]
    fn effective_user_prefers_token_owner_then_falls_back() {
        let cu = |u: &str| Some(Extension(CurrentUser(u.to_string())));
        let tok = |present: bool, owner: Option<&str>| {
            Some(Extension(ApiToken {
                present,
                id: None,
                owner: owner.map(String::from),
                scopes: vec![],
            }))
        };
        // Validated token with a non-empty owner -> that owner.
        assert_eq!(
            effective_user(&tok(true, Some("alice")), &cu("api")),
            Some("alice".to_string())
        );
        // Token present but empty/None owner -> get_current_user fallback.
        assert_eq!(
            effective_user(&tok(true, None), &cu("api")),
            Some("api".to_string())
        );
        assert_eq!(
            effective_user(&tok(true, Some("")), &cu("api")),
            Some("api".to_string())
        );
        // Not-a-token (cookie session) -> get_current_user.
        assert_eq!(
            effective_user(&tok(false, Some("alice")), &cu("bob")),
            Some("bob".to_string())
        );
        // No token extension at all -> get_current_user (here None).
        assert_eq!(effective_user(&None, &None), None);
    }

    // ── DB-backed helpers: temp DB per the established pattern ──
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
            "odysseus_session_routes_{tag}_{}_{}.db",
            std::process::id(),
            uuid::Uuid::new_v4().simple()
        );
        let path = dir.join(unique);
        let _ = std::fs::remove_file(&path);
        std::env::set_var("DATABASE_URL", format!("sqlite:///{}", path.display()));
        crate::core::database::create_all().unwrap();
        TmpDb(path)
    }

    fn seed_endpoint(
        conn: &rusqlite::Connection,
        id: &str,
        base_url: &str,
        api_key: Option<&str>,
        is_enabled: bool,
        owner: Option<&str>,
    ) {
        conn.execute(
            "INSERT INTO model_endpoints \
             (id, name, base_url, api_key, is_enabled, owner, created_at, updated_at) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
            rusqlite::params![id, id, base_url, api_key, is_enabled as i64, owner],
        )
        .unwrap();
    }

    fn seed_session(conn: &rusqlite::Connection, id: &str, owner: Option<&str>) {
        conn.execute(
            "INSERT INTO sessions (id, name, endpoint_url, model, owner, created_at, updated_at) \
             VALUES (?1, 'S', '', '', ?2, '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
            rusqlite::params![id, owner],
        )
        .unwrap();
    }

    #[test]
    fn model_endpoint_enabled_owner_scoped_filters() {
        let _guard = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db("ep_scope");
        let conn = crate::core::database::session_local().unwrap();
        // enabled + owned by alice (decrypt of plaintext returns it verbatim).
        seed_endpoint(&conn, "e1", "https://api.alice/v1", Some("sk-alice"), true, Some("alice"));
        // disabled, owned by alice.
        seed_endpoint(&conn, "e2", "https://api.alice/v2", Some("sk-x"), false, Some("alice"));
        // enabled, owned by bob.
        seed_endpoint(&conn, "e3", "https://api.bob/v1", Some("sk-bob"), true, Some("bob"));
        // enabled, shared (owner NULL).
        seed_endpoint(&conn, "e4", "https://api.shared/v1", None, true, None);
        drop(conn);

        // alice sees her enabled endpoint (key decrypted).
        let got = model_endpoint_enabled_owner_scoped("e1", Some("alice")).unwrap();
        assert_eq!(got, Some(("https://api.alice/v1".to_string(), Some("sk-alice".to_string()))));
        // disabled -> None (the 400 case).
        assert_eq!(model_endpoint_enabled_owner_scoped("e2", Some("alice")).unwrap(), None);
        // alice cannot resolve bob's endpoint.
        assert_eq!(model_endpoint_enabled_owner_scoped("e3", Some("alice")).unwrap(), None);
        // shared (NULL-owner) IS visible to alice via the owner_filter.
        assert_eq!(
            model_endpoint_enabled_owner_scoped("e4", Some("alice")).unwrap(),
            Some(("https://api.shared/v1".to_string(), None))
        );
        // unknown id -> None.
        assert_eq!(model_endpoint_enabled_owner_scoped("nope", Some("alice")).unwrap(), None);
        // single-user (no owner) unscoped path: bob's enabled endpoint resolves.
        assert_eq!(
            model_endpoint_enabled_owner_scoped("e3", None).unwrap(),
            Some(("https://api.bob/v1".to_string(), Some("sk-bob".to_string())))
        );
    }

    #[test]
    fn session_row_exists_detects_presence() {
        let _guard = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db("row_exists");
        let conn = crate::core::database::session_local().unwrap();
        seed_session(&conn, "s-here", Some("alice"));
        drop(conn);
        assert!(session_row_exists("s-here"));
        assert!(!session_row_exists("s-missing"));
    }
}
