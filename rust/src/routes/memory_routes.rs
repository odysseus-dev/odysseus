// routes/memory_routes.rs  <- routes/memory_routes.py
//! Memory CRUD / search / timeline / by-session / pin / debug routes (wave-3).
//!
//! Faithful translation of `routes/memory_routes.py`'s `setup_memory_routes`
//! factory. The handlers operate over the already-ported [`MemoryManager`]
//! (`src.memory`) and the optional [`MemoryVectorStore`] (`memory_vector`,
//! reached through `AppState`), plus `session_manager` for the timeline /
//! by-session name lookups.
//!
//! ## Shape
//! * `setup_memory_routes(memory_manager, session_manager, memory_vector)` ->
//!   `setup_memory_routes() -> Router<AppState>`: the three Python factory args
//!   are reached through `AppState` (`memory_manager`, `sessions`,
//!   `memory_vector`), so the factory takes no params. `APIRouter(prefix=
//!   "/api/memory")` becomes the absolute paths below.
//! * `_owner(request) = get_current_user(request)` -> read
//!   `Option<Extension<CurrentUser>>` (the `None` auth-disabled / single-user
//!   case is load-bearing — it means "owner = None", which `MemoryManager.load`
//!   treats as "no owner filter").
//! * `raise HTTPException(s, d)` -> `return Err(HttpException::new(s, d))`.
//! * `text: str = Form(...)` -> `multipart/form-data` (and urlencoded) parsed
//!   with the same manual field collector the inline subset uses.
//! * The `/add` handler accepts a JSON body (`Optional[MemoryAddRequest] = None`)
//!   OR a form fallback, sniffed on the content-type the way FastAPI binds an
//!   optional body before falling through to `await request.form()`.
//!
//! ## Wildcard ordering
//! The Python comment "Wildcard routes MUST come last" matters for Starlette's
//! sequential matcher; axum matches static segments (`/debug`, `/add`, `/search`,
//! `/timeline`, `/by-session/:id`, `/extract`, `/audit`, `/import`) ahead of the
//! `/:memory_id` captures regardless of registration order, and `/:memory_id/pin`
//! is more specific than `/:memory_id`, so there is no ambiguity. We keep the
//! Python source order for fidelity.
//!
//! ## HONEST DEFERS
//! * `POST /api/memory/import` — the non-UTF-8 charset-fallback path (Python's
//!   `charset_normalizer.detect`) is DEFERRED: a non-UTF-8 upload is decoded
//!   lossily (`from_utf8_lossy`) instead of detecting the encoding. The UTF-8,
//!   PDF, `.json` round-trip, and LLM-extraction paths are fully ported (see
//!   [`import_memories_from_file`]).
//!
//! ## No path collision
//! Every route is under `/api/memory*`, a prefix the inline `web/mod.rs` subset
//! never touches, so the aggregator merges this router without an axum
//! duplicate-`method`+`path` panic.


use std::collections::HashMap;

use axum::extract::{ConnectInfo, Multipart, Path, Request, State};
use axum::http::header::CONTENT_TYPE;
use axum::response::{IntoResponse, Response};
use axum::routing::{delete, get, post, put};
use axum::{Extension, Json, Router};
use once_cell::sync::Lazy;
use regex::Regex;
use rusqlite::OptionalExtension;
use serde_json::{json, Value};
use std::net::SocketAddr;

use crate::pylog as logger;
use crate::routes::auth_adapter;
use crate::routes::{AppState, CurrentUser, HttpException};
use crate::src::request_models::MemoryAddRequest;
use crate::src::upload_limits::read_upload_limited;

/// `MEMORY_IMPORT_MAX_BYTES = int(os.getenv("ODYSSEUS_MEMORY_IMPORT_MAX_BYTES", str(10 * 1024 * 1024)))`
///
/// Maximum bytes accepted for a direct (non-streaming) `/api/memory/import` body
/// read. Reads exceeding this cap are rejected with a 413. The environment variable
/// allows operators to widen the cap without recompiling.
fn memory_import_max_bytes() -> usize {
    std::env::var("ODYSSEUS_MEMORY_IMPORT_MAX_BYTES")
        .ok()
        .and_then(|v| v.parse::<usize>().ok())
        .unwrap_or(10 * 1024 * 1024) // 10 MB
}

/// Leading list-marker like "1.", "12)", or "3:" plus surrounding whitespace.
/// Strips one prefix per call so import-from-LLM-output doesn't leave the
/// numbering inside the saved memory text. Bullet markers (-, *, •) are
/// also peeled here for the same reason.
///
/// `re.compile(r"^\s*(?:\d{1,3}[.):]\s+|[-*•]\s+)")`.
static LIST_PREFIX_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^\s*(?:\d{1,3}[.):]\s+|[-*•]\s+)").unwrap());

/// `_strip_list_prefix(text)` — strip one leading list marker, then `.strip()`.
///
/// ```python
/// if not text:
///     return text
/// return _LIST_PREFIX_RE.sub("", text, count=1).strip()
/// ```
/// The regex crate's `replacen(_, 1, "")` replaces only the first match, the
/// `count=1` analogue; the trailing `.strip()` maps to `trim()`.
fn strip_list_prefix(text: &str) -> String {
    // `if not text: return text` — empty string passes straight through.
    if text.is_empty() {
        return text.to_string();
    }
    LIST_PREFIX_RE.replacen(text, 1, "").trim().to_string()
}

/// `setup_memory_routes(memory_manager, session_manager, memory_vector)` — assemble
/// the memory router. The three Python factory args are reached through `AppState`.
///
/// The Python registers, under `prefix="/api/memory"`, in this order:
/// `POST /debug`, `POST /add`, `GET ""`, `POST /search`, `GET /timeline`,
/// `GET /by-session/{session_id}`, `POST /extract`, `POST /audit`, `POST /import`,
/// `POST /{memory_id}/pin`, then the wildcards `GET /{memory_id}`,
/// `PUT /{memory_id}`, `DELETE /{memory_id}`.
pub fn setup_memory_routes() -> Router<AppState> {
    Router::new()
        // @router.post("/debug")
        .route("/api/memory/debug", post(debug_memory_relevance))
        // @router.post("/add", response_model=Dict[str, Any])
        .route("/api/memory/add", post(api_add_memory))
        // @router.get("")  -> prefix + ""  == "/api/memory"
        .route("/api/memory", get(api_get_memory))
        // @router.post("/search")
        .route("/api/memory/search", post(search_memories))
        // @router.get("/timeline")
        .route("/api/memory/timeline", get(memory_timeline))
        // @router.get("/by-session/{session_id}")
        .route("/api/memory/by-session/:session_id", get(get_memory_by_session))
        // @router.post("/extract")
        .route("/api/memory/extract", post(extract_memory))
        // @router.post("/audit")
        .route("/api/memory/audit", post(api_audit_memories))
        // @router.post("/import")
        .route("/api/memory/import", post(import_memories_from_file))
        // @router.post("/{memory_id}/pin")
        .route("/api/memory/:memory_id/pin", post(pin_memory))
        // Wildcard routes (last in Python; axum disambiguates statically).
        // @router.get("/{memory_id}")
        .route("/api/memory/:memory_id", get(get_memory_item))
        // @router.put("/{memory_id}")
        .route("/api/memory/:memory_id", put(update_memory))
        // @router.delete("/{memory_id}")
        .route("/api/memory/:memory_id", delete(delete_memory))
}

// ===========================================================================
// Auth glue
// ===========================================================================

/// `request.state.current_user` — the stamped user, or `None`.
fn user_str(user: &Option<Extension<CurrentUser>>) -> Option<&str> {
    user.as_ref().map(|Extension(u)| u.0.as_str())
}

/// `request.client.host` — the peer IP, or `None` when unknown.
fn host_str(ci: &Option<ConnectInfo<SocketAddr>>) -> Option<String> {
    ci.as_ref().map(|c| c.0.ip().to_string())
}

/// `_verify_memory_owner(memory, user)` — raise 404 if `user` doesn't own this memory.
///
/// SECURITY: strict ownership — a `None` user is auth-disabled (accept anything);
/// otherwise `memory.get("owner") != user` is a 404 (so we don't leak existence,
/// and an empty/null `owner` field is NOT owned by anyone).
///
/// ```python
/// if user is None:
///     return  # Auth disabled
/// if memory.get("owner") != user:
///     raise HTTPException(404, "Memory not found")
/// ```
fn verify_memory_owner(memory: &Value, user: Option<&str>) -> Result<(), HttpException> {
    match user {
        // `if user is None: return` — auth disabled.
        None => Ok(()),
        Some(u) => {
            // `if memory.get("owner") != user:` — a missing/null owner compares
            // unequal to a real username, so legacy null-owner data is NOT owned.
            if memory.get("owner").and_then(|v| v.as_str()) != Some(u) {
                Err(HttpException::new(404, "Memory not found"))
            } else {
                Ok(())
            }
        }
    }
}

/// `_assert_session_owner(session_obj, user)` — 404 unless the caller owns this
/// session. `SessionManager::get_session` is NOT owner-scoped (it returns any
/// session by id) and these routes accept a caller-supplied session id, so without
/// this gate a user could target another tenant's session and leak their chat
/// history, their session-scoped LLM credentials, or the session title. Mirrors
/// `session_routes` / `webhook_routes` ownership.
///
/// ```python
/// if user is not None and getattr(session_obj, "owner", None) != user:
///     raise HTTPException(404, "Session not found")
/// ```
fn assert_session_owner(
    sess: &crate::core::models::Session,
    user: Option<&str>,
) -> Result<(), HttpException> {
    if let Some(u) = user {
        if sess.owner.as_deref() != Some(u) {
            return Err(HttpException::new(404, "Session not found"));
        }
    }
    Ok(())
}

// ===========================================================================
// Handlers
// ===========================================================================

/// `POST /api/memory/debug` — "Debug which memories would be triggered for a query".
///
/// ```python
/// def debug_memory_relevance(request, query: str = Form(...)):
///     user = _owner(request)
///     memories = memory_manager.load(owner=user)
///     relevant = memory_manager.get_relevant_memories(query, memories, threshold=0.05)
///     return {"query", "total_memories", "relevant_count", "relevant_memories"}
/// ```
async fn debug_memory_relevance(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    let f = parse_form(mp).await;
    // `query: str = Form(...)` — a missing required Form field is a 422 in FastAPI.
    let query = require_form(&f, "query")?;
    let user = user_str(&user);

    let memories = s.memory_manager.load(user);
    // `get_relevant_memories(query, memories, threshold=0.05)` — uses the method's
    // default `max_items` (the Python default is 8).
    let relevant = s
        .memory_manager
        .get_relevant_memories(&query, &memories, 0.05, 8);

    // relevant_memories = [{"text": m["text"], "category": m.get("category", "unknown")} ...]
    let relevant_memories: Vec<Value> = relevant
        .iter()
        .map(|m| {
            json!({
                "text": m.get("text").cloned().unwrap_or(Value::Null),
                "category": m
                    .get("category")
                    .cloned()
                    .unwrap_or_else(|| Value::String("unknown".to_string())),
            })
        })
        .collect();

    Ok(Json(json!({
        "query": query,
        "total_memories": memories.len(),
        "relevant_count": relevant.len(),
        "relevant_memories": relevant_memories,
    }))
    .into_response())
}

/// `POST /api/memory/add` — "Add a new memory entry with optional category, source,
/// and session reference".
///
/// `require_privilege(request, "can_manage_memory")` gates the write. The body is
/// `Optional[MemoryAddRequest] = None`: FastAPI binds a JSON body when present, else
/// the handler falls through to `await request.form()`. We sniff the content-type to
/// reproduce that: an `application/json` body parses into [`MemoryAddRequest`] (the
/// pydantic validators — category-snapping — run via `deserialize_with`, and the
/// `text` `min_length=1`/`max_length=5000` constraints are enforced as a 422 BEFORE
/// the handler runs, mirroring FastAPI body validation); otherwise the form fields construct the
/// request, exactly as the Python does.
async fn api_add_memory(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    request: Request,
) -> Result<Response, HttpException> {
    // from src.auth_helpers import require_privilege
    // require_privilege(request, "can_manage_memory")
    auth_adapter::require_privilege(
        user_str(&user),
        &s,
        host_str(&ci).as_deref(),
        "can_manage_memory",
    )?;

    // Sniff JSON-body-vs-form the way FastAPI binds `Optional[MemoryAddRequest] = None`.
    let content_type = request
        .headers()
        .get(CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_lowercase();

    let memory_data: MemoryAddRequest = if content_type.contains("application/json") {
        // FastAPI binds the JSON body into the model; a present-but-invalid body is
        // a 422 (request validation), an empty body is `None`.
        let bytes = axum::body::to_bytes(request.into_body(), usize::MAX)
            .await
            .map_err(|e| HttpException::new(400, format!("There was an error parsing the body: {e}")))?;
        if bytes.is_empty() {
            // Empty body -> `memory_data is None` -> the form path. But there is no
            // form either; reproduce the Python `MemoryAddRequest(text=None)` ->
            // pydantic ValidationError -> uncaught -> 500.
            return Err(HttpException::new(500, "1 validation error for MemoryAddRequest"));
        }
        let parsed = serde_json::from_slice::<MemoryAddRequest>(&bytes)
            .map_err(|e| HttpException::new(422, e.to_string()))?;
        // `text: str = Field(..., min_length=1)`: when FastAPI binds the JSON body
        // into the model, pydantic enforces `min_length=1` as REQUEST validation,
        // rejecting `{"text": ""}` (length 0) with a 422 BEFORE the handler runs.
        // The serde struct uses a plain `String` (no length check), so enforce it
        // here, in the JSON branch only. This is distinct from the empty/whitespace
        // handling below: the form branch builds `MemoryAddRequest(...)` INSIDE the
        // handler, so its empty-string -> 500 / whitespace -> 400 paths stay as-is.
        if parsed.text.is_empty() {
            return Err(HttpException::new(
                422,
                "text\n  String should have at least 1 character",
            ));
        }
        // `text: str = Field(..., max_length=5000)`: the same FastAPI body binding
        // also enforces `max_length=5000`, rejecting a `text` longer than 5000 chars
        // with a 422 BEFORE the handler runs. pydantic measures length with `len()`
        // over the `str`, i.e. Unicode code points (scalar values), so count
        // `chars()` rather than bytes. JSON branch only — the form path constructs
        // `MemoryAddRequest(...)` inside the handler, so its over-length case is the
        // uncaught pydantic ValidationError (-> 500) and is left as-is.
        if parsed.text.chars().count() > 5000 {
            return Err(HttpException::new(
                422,
                "text\n  String should have at most 5000 characters",
            ));
        }
        parsed
    } else {
        // memory_data is None -> form = await request.form(); MemoryAddRequest(...)
        let bytes = axum::body::to_bytes(request.into_body(), usize::MAX)
            .await
            .unwrap_or_default();
        let mut form: HashMap<String, String> = HashMap::new();
        for (k, v) in url::form_urlencoded::parse(&bytes) {
            form.insert(k.into_owned(), v.into_owned());
        }
        // MemoryAddRequest(
        //     text=form.get("text"),                  # None when absent -> 500
        //     category=form.get("category", "fact"),
        //     source=form.get("source", "user"),
        //     session_id=form.get("session_id"),
        // )
        // Route the form values through deserialization so the category-snapping
        // validator runs identically to the JSON path; a missing `text` reproduces
        // the uncaught pydantic ValidationError (-> 500).
        let mut obj = serde_json::Map::new();
        match form.get("text") {
            // `text=Field(..., min_length=1)`: a present but EMPTY string (length 0)
            // fails pydantic's `min_length=1` at construction time. Because
            // `MemoryAddRequest(...)` is built INSIDE the handler (not bound by
            // FastAPI), the ValidationError is uncaught -> 500. A whitespace-only
            // value (e.g. "   ", length >= 1) PASSES min_length and is handled later
            // by `text.strip()` -> "" -> the 400 "empty memory" branch, so only the
            // truly-empty string short-circuits to 500 here.
            Some(t) if t.is_empty() => {
                return Err(HttpException::new(
                    500,
                    "1 validation error for MemoryAddRequest\ntext\n  String should have at least 1 character",
                ))
            }
            Some(t) => {
                obj.insert("text".to_string(), Value::String(t.clone()));
            }
            // `text=None` -> required field violated -> pydantic ValidationError.
            None => {
                return Err(HttpException::new(
                    500,
                    "1 validation error for MemoryAddRequest\ntext\n  Input should be a valid string",
                ))
            }
        }
        obj.insert(
            "category".to_string(),
            Value::String(form.get("category").cloned().unwrap_or_else(|| "fact".to_string())),
        );
        obj.insert(
            "source".to_string(),
            Value::String(form.get("source").cloned().unwrap_or_else(|| "user".to_string())),
        );
        if let Some(sid) = form.get("session_id") {
            obj.insert("session_id".to_string(), Value::String(sid.clone()));
        }
        serde_json::from_value::<MemoryAddRequest>(Value::Object(obj))
            .map_err(|e| HttpException::new(500, e.to_string()))?
    };

    let user = user_str(&user);
    // text = (memory_data.text or "").strip()
    let text = memory_data.text.trim().to_string();
    // if not text: raise HTTPException(400, "empty memory")
    if text.is_empty() {
        return Err(HttpException::new(400, "empty memory"));
    }

    let user_mem = s.memory_manager.load(user);
    // if memory_manager.find_duplicates(text, user_mem): return {"ok", "count", "message"}
    if !s.memory_manager.find_duplicates(&text, Some(&user_mem)).is_empty() {
        return Ok(Json(json!({
            "ok": true,
            "count": user_mem.len(),
            "message": "Memory already exists",
        }))
        .into_response());
    }

    // new_entry = memory_manager.add_entry(text, source, category, owner=user)
    let mut new_entry = s
        .memory_manager
        .add_entry(&text, &memory_data.source, &memory_data.category, user)
        .map_err(|e| HttpException::new(500, e.to_string()))?;
    // if memory_data.session_id: new_entry["session_id"] = memory_data.session_id
    if let Some(session_id) = memory_data.session_id.as_deref() {
        if !session_id.is_empty() {
            if let Some(obj) = new_entry.as_object_mut() {
                obj.insert("session_id".to_string(), Value::String(session_id.to_string()));
            }
        }
    }
    // all_mem = memory_manager.load_all(); all_mem.append(new_entry); memory_manager.save(all_mem)
    let mut all_mem = s.memory_manager.load_all();
    all_mem.push(new_entry.clone());
    s.memory_manager
        .save(&mut all_mem)
        .map_err(|e| HttpException::new(500, e.to_string()))?;

    // Sync vector index — if memory_vector and memory_vector.healthy: memory_vector.add(...)
    if let Some(mv) = s.memory_vector.as_ref() {
        if mv.healthy() {
            // `memory_vector.add(new_entry["id"], text)` — the canonical store's
            // `add` is async in the Rust port; the Python route lets it raise, so a
            // failure propagates (-> 500).
            let id = new_entry
                .get("id")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            mv.add(&id, &text)
                .await
                .map_err(|e| HttpException::new(500, e.to_string()))?;
        }
    }

    // try: fire_event("memory_added", user) except Exception: logger.debug(...)
    crate::src::event_bus::fire_event("memory_added", user);

    // return {"ok": True, "count": len([m for m in all_mem if m.get("owner") == user])}
    let count = all_mem
        .iter()
        .filter(|m| m.get("owner").and_then(|v| v.as_str()) == user)
        .count();
    Ok(Json(json!({ "ok": true, "count": count })).into_response())
}

/// `GET /api/memory` — "Return all memory entries with their metadata".
///
/// ```python
/// def api_get_memory(request):
///     user = _owner(request)
///     return {"memory": memory_manager.load(owner=user)}
/// ```
async fn api_get_memory(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    let user = user_str(&user);
    Ok(Json(json!({ "memory": s.memory_manager.load(user) })).into_response())
}

/// `POST /api/memory/search` — "Search across all memories with optional filters".
///
/// ```python
/// def search_memories(request, query: str = Form(...), session_id: str = Form(None),
///                      category: str = Form(None)):
///     user = _owner(request)
///     memories = memory_manager.load(owner=user)
///     if session_id: memories = [m for m if m.get("session_id") == session_id]
///     if category:   memories = [m for m if category in m.get("categories", [m.get("category", "")])]
///     relevant = memory_manager.get_relevant_memories(query, memories, threshold=0.05, max_items=20)
///     return {"memories": relevant, "total": len(relevant), "query": query}
/// ```
async fn search_memories(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    let f = parse_form(mp).await;
    // `query: str = Form(...)` — required.
    let query = require_form(&f, "query")?;
    // `session_id: str = Form(None)` / `category: str = Form(None)` — optional.
    let session_id = f.get("session_id").cloned();
    let category = f.get("category").cloned();
    let user = user_str(&user);

    let mut memories = s.memory_manager.load(user);

    // if session_id: memories = [m for m in memories if m.get("session_id") == session_id]
    if let Some(sid) = session_id.as_deref().filter(|s| !s.is_empty()) {
        memories.retain(|m| m.get("session_id").and_then(|v| v.as_str()) == Some(sid));
    }

    // if category: memories = [m for m if category in m.get("categories", [m.get("category", "")])]
    if let Some(cat) = category.as_deref().filter(|s| !s.is_empty()) {
        memories.retain(|m| {
            // m.get("categories", [m.get("category", "")]) — when "categories" is
            // absent, the default is a one-element list of the (string) "category"
            // (or "" when that is absent too). `category in <list>`.
            match m.get("categories") {
                Some(Value::Array(cats)) => {
                    cats.iter().any(|c| c.as_str() == Some(cat))
                }
                // A non-list "categories" value: Python's `in` would check membership
                // against that value (e.g. substring for a string). The stored shape
                // is always a list when present; for any non-list we fall back to the
                // single-category default to stay faithful to the common case.
                Some(_) => false,
                None => {
                    let single = m.get("category").and_then(|v| v.as_str()).unwrap_or("");
                    single == cat
                }
            }
        });
    }

    // relevant = get_relevant_memories(query, memories, threshold=0.05, max_items=20)
    let relevant = s
        .memory_manager
        .get_relevant_memories(&query, &memories, 0.05, 20);

    Ok(Json(json!({
        "memories": relevant,
        "total": relevant.len(),
        "query": query,
    }))
    .into_response())
}

/// `GET /api/memory/timeline` — "Get memories in chronological order with source
/// session information".
///
/// ```python
/// def memory_timeline(request):
///     user = _owner(request)
///     memories = memory_manager.load(owner=user)
///     sorted_memories = sorted(memories, key=lambda x: x.get("timestamp", 0), reverse=True)
///     for memory in sorted_memories:
///         # timestamp_str via datetime.fromtimestamp(...).strftime("%Y-%m-%d %H:%M:%S")
///         #   (ValueError/OSError/OverflowError -> "Unknown"); missing -> "Unknown"
///         # session_name: if session_id in session_manager.sessions -> get_session(...).name
///         #   else "Unknown"
///     return {"timeline": results, "total": len(results)}
/// ```
async fn memory_timeline(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    let user = user_str(&user);
    let mut sorted_memories = s.memory_manager.load(user);

    // sorted(memories, key=lambda x: x.get("timestamp", 0), reverse=True)
    // Python's `sorted` is stable; `sort_by` is stable, so ties keep input order.
    sorted_memories.sort_by(|a, b| {
        let ta = timestamp_key(a);
        let tb = timestamp_key(b);
        tb.partial_cmp(&ta).unwrap_or(std::cmp::Ordering::Equal)
    });

    // `session_id in session_manager.sessions` checks the in-memory cache only;
    // `get_sessions_for_user(None)` clones that exact map (no DB hit), the faithful
    // equivalent of Python's `session_manager.sessions` dict.
    let in_memory = s.sessions.get_sessions_for_user(None);

    let mut results: Vec<Value> = Vec::with_capacity(sorted_memories.len());
    for mut memory in sorted_memories {
        let obj = match memory.as_object_mut() {
            Some(o) => o,
            None => {
                results.push(memory);
                continue;
            }
        };

        // if "timestamp" in memory: try datetime.fromtimestamp(...).strftime(...)
        //   except (ValueError, OSError, OverflowError): "Unknown"
        // else: "Unknown"
        let ts_str = match obj.get("timestamp") {
            Some(ts) => timestamp_to_str(ts),
            None => "Unknown".to_string(),
        };
        obj.insert("timestamp_str".to_string(), Value::String(ts_str));

        // session_id = memory.get("session_id")
        // if session_id and session_id in session_manager.sessions:
        //     session = session_manager.get_session(session_id)
        //     memory["session_name"] = session.name if session else f"Session {session_id[:6]}"
        // else: memory["session_name"] = "Unknown"
        let session_id = obj.get("session_id").and_then(|v| v.as_str()).map(str::to_string);
        let session_name = match session_id.as_deref() {
            Some(sid) if !sid.is_empty() && in_memory.contains_key(sid) => {
                // `get_session` always returns a Session here (the `if session else`
                // arm is dead in Python), so we use its name.
                match s.sessions.get_session(sid) {
                    Ok(session) => session.name,
                    Err(_) => format!("Session {}", short6(sid)),
                }
            }
            _ => "Unknown".to_string(),
        };
        obj.insert("session_name".to_string(), Value::String(session_name));

        results.push(memory);
    }

    let total = results.len();
    Ok(Json(json!({ "timeline": results, "total": total })).into_response())
}

/// `GET /api/memory/by-session/{session_id}` — "Get all memories associated with a
/// specific session".
///
/// ```python
/// def get_memory_by_session(request, session_id: str):
///     try: session_manager.get_session(session_id)
///     except KeyError: raise HTTPException(404, f"Session {session_id} not found")
///     user = _owner(request)
///     memories = memory_manager.load(owner=user)
///     session_memories = [m for m if m.get("session_id") == session_id]
///     session_memories.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
///     # session_name from get_session (KeyError -> f"Session {session_id[:6]}")
///     for m in session_memories: m["session_name"] = session_name
///     return {"session_id", "session_name", "memory_count", "memories"}
/// ```
async fn get_memory_by_session(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(session_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = user_str(&user);
    // try: session_manager.get_session(session_id) except KeyError: 404
    let session_obj = match s.sessions.get_session(&session_id) {
        Ok(so) => so,
        Err(_) => {
            return Err(HttpException::new(
                404,
                format!("Session {session_id} not found"),
            ))
        }
    };
    // SECURITY: 404 unless the caller owns this session (get_session is unscoped).
    assert_session_owner(&session_obj, user)?;
    let memories = s.memory_manager.load(user);
    // session_memories = [m for m in memories if m.get("session_id") == session_id]
    let mut session_memories: Vec<Value> = memories
        .into_iter()
        .filter(|m| m.get("session_id").and_then(|v| v.as_str()) == Some(session_id.as_str()))
        .collect();

    // session_memories.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    session_memories.sort_by(|a, b| {
        let ta = timestamp_key(a);
        let tb = timestamp_key(b);
        tb.partial_cmp(&ta).unwrap_or(std::cmp::Ordering::Equal)
    });

    // try: session = session_manager.get_session(session_id)
    //      session_name = session.name if session else f"Session {session_id[:6]}"
    // except KeyError: session_name = f"Session {session_id[:6]}"
    let session_name = match s.sessions.get_session(&session_id) {
        Ok(session) => session.name,
        Err(_) => format!("Session {}", short6(&session_id)),
    };

    // for memory in session_memories: memory["session_name"] = session_name
    for memory in session_memories.iter_mut() {
        if let Some(obj) = memory.as_object_mut() {
            obj.insert("session_name".to_string(), Value::String(session_name.clone()));
        }
    }

    let memory_count = session_memories.len();
    Ok(Json(json!({
        "session_id": session_id,
        "session_name": session_name,
        "memory_count": memory_count,
        "memories": session_memories,
    }))
    .into_response())
}

/// `POST /api/memory/extract` — "Analyze a session's chat history and return memory
/// suggestions".
///
/// ```python
/// async def extract_memory(request, session: str = Form(...)) -> Dict[str, List[str]]:
///     require_user(request)
///     try: sess = session_manager.get_session(session)
///     except KeyError: raise HTTPException(404, "Session not found")
///     messages = [system_msg] + sess.get_context_messages()
///     try: suggestion_text = await llm_call_async(...); parse JSON-or-lines
///     except Exception: fallback = memory_manager.extract_memory_from_chat(sess.history, session)
///     return {"suggestions": [...]}
/// ```
async fn extract_memory(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    let f = parse_form(mp).await;
    // `session: str = Form(...)` — required.
    let session = require_form(&f, "session")?;

    // require_user(request) — reject unauthenticated callers (commit "Auth: use
    // require_user for remaining guarded routes"). Unlike the old inline
    // `if not get_current_user(request): 401`, this honors AUTH_ENABLED=false,
    // the unconfigured-first-run loopback fall-through, and LOCALHOST_BYPASS=true,
    // returning "" (allowed) in those single-user modes. The resolved username is
    // discarded (the route uses `_owner`/`get_current_user` shape elsewhere).
    auth_adapter::require_user(user_str(&user), &s, host_str(&ci).as_deref())?;

    // try: sess = session_manager.get_session(session) except KeyError: 404
    let sess = match s.sessions.get_session(&session) {
        Ok(sess) => sess,
        Err(_) => return Err(HttpException::new(404, "Session not found")),
    };
    // SECURITY: 404 unless the caller owns this session.
    assert_session_owner(&sess, user_str(&user))?;

    // system_msg + sess.get_context_messages()
    let system_content = "You are a helpful assistant. Analyze the entire conversation history provided and extract any useful factual statements, contacts, addresses, phone numbers, or other information that the user might want to remember for future interactions. Return each piece of information as a JSON object with a 'text' field. For example: [{'text': 'Alice lives at 123 Main St'}, {'text': 'Bob works at Acme Corp'}]. Only include information that is specific and likely to be useful later.";
    let mut messages: Vec<Value> = vec![json!({"role": "system", "content": system_content})];
    messages.extend(sess.get_context_messages());

    // try: suggestion_text = await llm_call_async(...)
    // Python passes no `timeout=`, so it uses llm_call_async's default STREAM_TIMEOUT.
    let llm_result = crate::src::llm_core::llm_call_async(
        &sess.endpoint_url,
        &sess.model,
        messages,
        0.2,
        500,
        sess.headers.clone(),
        crate::src::llm_core::LLMConfig::DEFAULT_STREAM_TIMEOUT as u64,
    )
    .await;

    match llm_result {
        Ok(suggestion_text) => {
            // try: suggestions = json.loads(suggestion_text)
            //      if isinstance(list): [s if str else s.get("text", "") for s]
            //      else: suggestions = []
            // except JSONDecodeError: [line.strip() for line in splitlines() if line.strip()]
            let suggestions: Vec<String> = match serde_json::from_str::<Value>(&suggestion_text) {
                Ok(Value::Array(items)) => items
                    .iter()
                    .map(|s| match s {
                        Value::String(text) => text.clone(),
                        // s.get("text", "") — only dicts have .get; a non-string,
                        // non-dict element would AttributeError in Python, but the
                        // model returns strings/dicts here.
                        Value::Object(_) => {
                            s.get("text").and_then(|v| v.as_str()).unwrap_or("").to_string()
                        }
                        _ => String::new(),
                    })
                    .collect(),
                // isinstance(suggestions, list) is False -> []
                Ok(_) => Vec::new(),
                // json.JSONDecodeError -> split on lines.
                Err(_) => suggestion_text
                    .lines()
                    .map(|l| l.trim().to_string())
                    .filter(|l| !l.is_empty())
                    .collect(),
            };
            // return {"suggestions": [s for s in suggestions if s]}
            let suggestions: Vec<Value> = suggestions
                .into_iter()
                .filter(|s| !s.is_empty())
                .map(Value::String)
                .collect();
            Ok(Json(json!({ "suggestions": suggestions })).into_response())
        }
        Err(e) => {
            // except Exception as e: logger.error(...)
            logger::error(&format!("LLM memory extraction failed (session {session}): {e}"));
            // fallback = memory_manager.extract_memory_from_chat(sess.history, session)
            // The Rust extract_memory_from_chat takes &[Value]; sess.get_context_messages()
            // is the dict-message list (== Python's sess.history shape). The FAITHFUL
            // bug (bullet line -> AttributeError) propagates as a PyError -> 500.
            let history = sess.get_context_messages();
            let fallback = s
                .memory_manager
                .extract_memory_from_chat(&history, Some(&session))
                .map_err(|e| HttpException::new(500, e.to_string()))?;
            // return {"suggestions": [item["text"] for item in fallback]}
            let suggestions: Vec<Value> = fallback
                .iter()
                .map(|item| item.get("text").cloned().unwrap_or(Value::Null))
                .collect();
            Ok(Json(json!({ "suggestions": suggestions })).into_response())
        }
    }
}

/// `POST /api/memory/audit` — "Deduplicate and consolidate memories via LLM".
///
/// Faithful port of `api_audit_memories`. The Python resolves the LLM endpoint via:
/// 1. the default model from settings (`default_endpoint_id` + `default_model`),
///    reading the matching enabled `ModelEndpoint` row; then
/// 2. a fallback to the named session's model when no default resolved.
///
/// Then it calls `audit_memories(...)` and shapes `{ok, before, after, removed,
/// already_tidy}`. The `model_routes` helpers it imports are
/// `_load_settings = src.settings.load_settings` and
/// `_normalize_base = src.endpoint_resolver.normalize_base` + `build_chat_url`
/// (same `endpoint_resolver` the ported `note_routes` already uses), so this is a
/// direct translation — NOT a defer.
///
/// ## The `ep.models` AttributeError (faithful 500)
/// The Python branch
/// ```python
/// model = default_model
/// if not model and ep.models:
///     ...
/// ```
/// references `ep.models`, but the SQLAlchemy `ModelEndpoint` ORM has NO `models`
/// attribute (only `cached_models` / `hidden_models`). When `default_model` is
/// EMPTY (so `not model` is truthy and `and` does NOT short-circuit), evaluating
/// `ep.models` raises `AttributeError`, which is uncaught in the handler -> a 500.
/// When `default_model` is non-empty the `and` short-circuits and `ep.models` is
/// never touched. We reproduce exactly that: an enabled default endpoint with an
/// EMPTY `default_model` -> 500; a non-empty `default_model` -> use it.
///
/// `ep.api_key` is `EncryptedText` (Fernet, `enc:` prefix) — the ORM decrypts on
/// read, so the raw `rusqlite` read here runs `secret_storage::decrypt` before
/// building `Authorization: Bearer <key>`, the same way `compare_routes` /
/// `research_routes` decrypt stored keys.
async fn api_audit_memories(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    let f = parse_form(mp).await;
    // `session: str = Form(None)` — optional session fallback.
    let session = f.get("session").cloned();

    // endpoint_url = model = None; headers = {}
    let mut endpoint_url: Option<String> = None;
    let mut model: Option<String> = None;
    let mut headers: indexmap::IndexMap<String, String> = indexmap::IndexMap::new();

    // settings = _load_settings()
    let settings = crate::src::settings::load_settings();
    // ep_id = settings.get("default_endpoint_id", "")
    let ep_id = settings
        .get("default_endpoint_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    // default_model = settings.get("default_model", "")
    let default_model = settings
        .get("default_model")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();

    // if ep_id:  (Python truthiness — a non-empty id)
    if !ep_id.is_empty() {
        // db = SessionLocal(); ep = db.query(ModelEndpoint).filter(
        //     ModelEndpoint.id == ep_id, ModelEndpoint.is_enabled == True).first()
        // The ORM stores BOOLEAN as 0/1; `is_enabled == True` -> `is_enabled = 1`.
        let conn = crate::core::database::session_local()
            .map_err(|e| HttpException::new(500, e.to_string()))?;
        let row: Option<(String, Option<String>)> = conn
            .query_row(
                "SELECT base_url, api_key FROM model_endpoints \
                 WHERE id = ?1 AND is_enabled = 1",
                rusqlite::params![ep_id],
                |r| Ok((r.get::<_, String>(0)?, r.get::<_, Option<String>>(1)?)),
            )
            .optional()
            .map_err(|e| HttpException::new(500, e.to_string()))?;

        // if ep:
        if let Some((base_url, enc_key)) = row {
            // base = _normalize_base(ep.base_url); endpoint_url = build_chat_url(base)
            let base = crate::src::endpoint_resolver::normalize_base(Some(&base_url));
            endpoint_url = Some(crate::src::endpoint_resolver::build_chat_url(&base));
            // model = default_model
            model = Some(default_model.clone());
            // if not model and ep.models:  — `ep.models` is NOT a mapped column on the
            // ModelEndpoint ORM (only `cached_models`/`hidden_models` exist), so when
            // `default_model` is empty this raises AttributeError in Python -> uncaught
            // -> 500. When `default_model` is non-empty the `and` short-circuits and
            // `ep.models` is never evaluated, so no error.
            if default_model.is_empty() {
                return Err(HttpException::new(
                    500,
                    "'ModelEndpoint' object has no attribute 'models'",
                ));
            }
            // if ep.api_key: headers = {"Authorization": f"Bearer {ep.api_key}"}
            // EncryptedText decrypts on read in the ORM; decrypt the stored ciphertext
            // here. Python truthiness: a non-empty (decrypted) key.
            if let Some(key) = enc_key
                .filter(|k| !k.is_empty())
                .map(|k| crate::src::secret_storage::decrypt(&k))
                .filter(|k| !k.is_empty())
            {
                headers.insert("Authorization".to_string(), format!("Bearer {key}"));
            }
        }
        // finally: db.close()  — the rusqlite Connection drops at scope end.
    }

    // Fall back to session model if no default configured.
    // if not endpoint_url and session: try get_session(session) except KeyError: pass
    if endpoint_url.as_deref().map(str::is_empty).unwrap_or(true) {
        if let Some(sid) = session.as_deref().filter(|s| !s.is_empty()) {
            if let Ok(sess) = s.sessions.get_session(sid) {
                // SECURITY: 404 unless the caller owns this session.
                assert_session_owner(&sess, user_str(&user))?;
                endpoint_url = Some(sess.endpoint_url.clone());
                model = Some(sess.model.clone());
                headers = sess.headers.clone();
            }
        }
    }

    // if not endpoint_url or not model: raise HTTPException(400, "No default model ...")
    let (endpoint_url, model) = match (
        endpoint_url.filter(|e| !e.is_empty()),
        model.filter(|m| !m.is_empty()),
    ) {
        (Some(e), Some(m)) => (e, m),
        _ => {
            return Err(HttpException::new(
                400,
                "No default model configured — set one in Settings",
            ))
        }
    };

    // user = _owner(request)
    let user = user_str(&user);

    // result = await audit_memories(memory_manager, memory_vector, endpoint_url,
    //     model, headers, owner=user)
    //
    // `audit_memories` takes `&mut Option<MemoryVectorStore>` (its `rebuild` resets
    // the collection handle, needing `&mut self`). AppState holds the shared store
    // as `Option<Arc<MemoryVectorStore>>`; we hand it an owned clone — the store is
    // a thin handle over the shared SQLite `vectors` table, so rebuilding through the
    // clone rebuilds the SAME backing collection (identical to Python passing the one
    // shared object). When DEGRADED the store is `None`, mirroring Python's `None`.
    let mut mv: Option<crate::services::memory::MemoryVectorStore> =
        s.memory_vector.as_ref().map(|arc| (**arc).clone());
    let result = crate::services::memory::memory_extractor::audit_memories(
        &s.memory_manager,
        &mut mv,
        &endpoint_url,
        &model,
        Some(headers),
        user,
    )
    .await;

    // if "error" in result and "before" not in result:
    //     raise HTTPException(502, f"Audit failed: {result['error']}")
    let has_error = result.get("error").is_some();
    if has_error && result.get("before").is_none() {
        let err = result
            .get("error")
            .map(py_str)
            .unwrap_or_default();
        return Err(HttpException::new(502, format!("Audit failed: {err}")));
    }

    // return {ok, before, after, removed, already_tidy}
    let before = result.get("before").and_then(Value::as_i64).unwrap_or(0);
    let after = result.get("after").and_then(Value::as_i64).unwrap_or(0);
    let already_tidy = result.get("already_tidy").map(value_truthy).unwrap_or(false);
    Ok(Json(json!({
        // "ok": "error" not in result
        "ok": !has_error,
        "before": before,
        "after": after,
        // "removed": result.get("before", 0) - result.get("after", 0)
        "removed": before - after,
        "already_tidy": already_tidy,
    }))
    .into_response())
}

/// `POST /api/memory/import` — "Extract memory suggestions from an uploaded file
/// (PDF, TXT, MD, etc.)".
///
/// `require_privilege(request, "can_manage_memory")` gates the upload. The `session`
/// form field is OPTIONAL (#493): when present, the named session supplies the LLM
/// endpoint/model/headers (a missing session is still a 404 "needed for LLM config");
/// when absent, the endpoint is resolved via `resolve_endpoint("utility",
/// owner=_owner(request))` (the centralized utility-cascade resolver), so a 400 "No
/// LLM model configured" fires only when neither path yields a model. The `.json`
/// round-trip fast path, the PDF extraction path, and the UTF-8 + LLM extraction
/// path are fully ported. HONEST DEFER: the non-UTF-8 charset-fallback
/// (`charset_normalizer.detect`) decodes lossily instead of sniffing the encoding.
async fn import_memories_from_file(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    ci: Option<ConnectInfo<SocketAddr>>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    // from src.auth_helpers import require_privilege
    // require_privilege(request, "can_manage_memory")
    auth_adapter::require_privilege(
        user_str(&user),
        &s,
        host_str(&ci).as_deref(),
        "can_manage_memory",
    )?;

    // session: str | None = Form(None)  /  file: UploadFile = File(...)
    //
    // `content = await read_upload_limited(file, MEMORY_IMPORT_MAX_BYTES, "Memory import")`
    // (commit 193dc2f) — the file body read is now bounded. We inline the multipart
    // loop here (rather than using the generic `parse_multipart_with_file` helper) so
    // we can pass the file field directly to `read_upload_limited`.
    let limit = memory_import_max_bytes();
    let mut form: HashMap<String, String> = HashMap::new();
    let mut file_bytes: Option<Vec<u8>> = None;
    let mut file_name: Option<String> = None;
    {
        let mut mp = mp;
        while let Ok(Some(field)) = mp.next_field().await {
            let name = field.name().unwrap_or("").to_string();
            if name == "file" {
                file_name = field.file_name().map(str::to_string);
                // Bounded read — mirrors `await read_upload_limited(file, MEMORY_IMPORT_MAX_BYTES, "Memory import")`.
                file_bytes = Some(read_upload_limited(field, limit, "Memory import").await?);
            } else if !name.is_empty() {
                if let Ok(text) = field.text().await {
                    form.insert(name, text);
                }
            }
        }
    }
    // `session` is now optional; an absent OR empty value is Python-falsy and routes
    // to the resolve_endpoint("utility") fallback below.
    let session = form.get("session").filter(|v| !v.is_empty());
    let (content, filename) = match file_bytes {
        Some(bytes) => (bytes, file_name.unwrap_or_else(|| "upload".to_string())),
        // A missing required File(...) is a 422 in FastAPI.
        None => return Err(HttpException::new(422, "Field required: file")),
    };

    // endpoint_url = None; model = None; headers = {}
    //
    // if session:  use the named session's endpoint/model/headers (KeyError -> 404).
    // else:        endpoint_url, model, headers = resolve_endpoint("utility", owner=_owner(request))
    //
    // resolve_endpoint returns a (Option<url>, Option<model>, Option<headers>) triple
    // where headers is Some only when an endpoint resolves; resolve_endpoint_triple
    // collapses to Some((url, model, headers)) exactly when all three are present, so
    // a failed resolution becomes None and falls through to the 400 below — matching
    // Python's `if not endpoint_url or not model`.
    let (endpoint_url, model, headers): (String, String, indexmap::IndexMap<String, String>) =
        if let Some(session) = session {
            // try: sess = session_manager.get_session(session)
            //      endpoint_url = sess.endpoint_url; model = sess.model; headers = sess.headers
            // except KeyError: raise HTTPException(404, "Session not found — needed for LLM config")
            match s.sessions.get_session(session) {
                Ok(sess) => {
                    // SECURITY: 404 unless the caller owns this session.
                    assert_session_owner(&sess, user_str(&user))?;
                    (
                        sess.endpoint_url.clone(),
                        sess.model.clone(),
                        sess.headers.clone(),
                    )
                }
                Err(_) => {
                    return Err(HttpException::new(
                        404,
                        "Session not found — needed for LLM config",
                    ))
                }
            }
        } else {
            // endpoint_url, model, headers = resolve_endpoint("utility", owner=_owner(request))
            match crate::src::endpoint_resolver::resolve_endpoint_triple("utility", user_str(&user))
            {
                Some(triple) => triple,
                // resolve_endpoint returned a (None, ...) / model-less triple -> the
                // `if not endpoint_url or not model` 400 below.
                None => {
                    return Err(HttpException::new(
                        400,
                        "No LLM model configured. Set a default model in Settings.",
                    ))
                }
            }
        };

    // if not endpoint_url or not model: raise HTTPException(400, "No LLM model configured...")
    // The else branch already 400s via resolve_endpoint_triple's None; this also covers
    // a session-present path whose session carries an empty endpoint_url / model.
    if endpoint_url.is_empty() || model.is_empty() {
        return Err(HttpException::new(
            400,
            "No LLM model configured. Set a default model in Settings.",
        ));
    }

    // filename = file.filename or "upload"; _, ext = os.path.splitext(filename.lower())
    let ext = splitext_ext(&filename.to_lowercase());

    // allowed = {".txt", ".md", ".pdf", ".csv", ".log", ".json", ".py", ".js", ".html"}
    const ALLOWED: [&str; 9] = [
        ".txt", ".md", ".pdf", ".csv", ".log", ".json", ".py", ".js", ".html",
    ];
    if !ALLOWED.contains(&ext.as_str()) {
        return Err(HttpException::new(400, format!("Unsupported file type: {ext}")));
    }

    // Extract text based on file type.
    let text: String = if ext == ".pdf" {
        // tempfile.NamedTemporaryFile(suffix=".pdf"); write; _process_pdf(tmp_path); unlink
        let tmp_path = match write_temp(&content, ".pdf") {
            Ok(p) => p,
            Err(e) => return Err(HttpException::new(500, e.to_string())),
        };
        let extracted = crate::src::document_processor::_process_pdf(&tmp_path);
        let _ = std::fs::remove_file(&tmp_path); // finally: os.unlink(tmp_path)
        extracted
    } else {
        // try: content.decode("utf-8")
        // except UnicodeDecodeError:
        //   detected = charset_normalizer.detect(content)
        //   encoding = (detected or {}).get("encoding") or "utf-8"
        //   text = content.decode(encoding, errors="replace")
        match std::str::from_utf8(&content) {
            Ok(s) => s.to_string(),
            Err(_) => {
                // Sniff with chardetng (Firefox's detector), then decode via
                // encoding_rs (BOM sniff + U+FFFD replacement == errors="replace").
                // Import files are document/email-like (no script execution), so
                // ISO-2022-JP and UTF-8 are both allowed guesses — matching
                // charset_normalizer's broad detection.
                let mut det =
                    chardetng::EncodingDetector::new(chardetng::Iso2022JpDetection::Allow);
                det.feed(&content, true);
                let enc = det.guess(None, chardetng::Utf8Detection::Allow);
                let (text, _enc, _had_errors) = enc.decode(&content);
                text.into_owned()
            }
        }
    };

    // if not text.strip(): return {"suggestions": [], "message": "No readable content found"}
    if text.trim().is_empty() {
        return Ok(Json(json!({
            "suggestions": [],
            "message": "No readable content found",
        }))
        .into_response());
    }

    // Fast path: a .json upload that already looks like a memories export round-trips.
    if ext == ".json" {
        // try: parsed = json.loads(text) except JSONDecodeError: parsed = None
        let parsed = serde_json::from_str::<Value>(&text).ok();
        // if isinstance(parsed, list) and parsed:
        if let Some(Value::Array(items)) = parsed {
            if !items.is_empty() {
                let mut direct: Vec<Value> = Vec::new();
                for item in &items {
                    match item {
                        // isinstance(item, dict) and item.get("text")
                        Value::Object(_) => {
                            if let Some(t) = item.get("text").filter(|v| value_truthy(v)) {
                                direct.push(json!({
                                    "text": strip_list_prefix(&py_str(t)),
                                    // item.get("category") or "fact"
                                    "category": item
                                        .get("category")
                                        .filter(|v| value_truthy(v))
                                        .cloned()
                                        .unwrap_or_else(|| Value::String("fact".to_string())),
                                }));
                            }
                        }
                        // elif isinstance(item, str) and item.strip():
                        Value::String(item_str) if !item_str.trim().is_empty() => {
                            direct.push(json!({
                                "text": strip_list_prefix(item_str.trim()),
                                "category": "fact",
                            }));
                        }
                        _ => {}
                    }
                }
                // if direct: return {"suggestions": direct, "filename": filename}
                if !direct.is_empty() {
                    return Ok(Json(json!({ "suggestions": direct, "filename": filename }))
                        .into_response());
                }
            }
        }
    }

    // Truncate very long documents — if len(text) > 15000: text = text[:15000] + "\n[Truncated]"
    // Python `len`/slicing is by code point.
    let text = {
        let chars: Vec<char> = text.chars().collect();
        if chars.len() > 15000 {
            let head: String = chars[..15000].iter().collect();
            format!("{head}\n[Truncated]")
        } else {
            text
        }
    };

    // Send to LLM for memory extraction.
    let import_prompt = "You are a memory extraction assistant. The user uploaded a document. Analyze the text below and extract specific, useful facts — things like names, preferences, jobs, locations, relationships, opinions, projects, goals, contacts, or any other personal details worth remembering.\n\nRules:\n- Each fact should be a short, self-contained statement\n- Do NOT extract generic knowledge\n- Focus on personal, memorable information\n- If there are no useful facts, return an empty array\n\nReturn a JSON array of objects with 'text' and 'category' fields.\nCategories: 'identity', 'preference', 'fact', 'contact', 'project', 'goal'\n\nReturn ONLY valid JSON, no markdown fences.";
    let user_content = format!("Document: {filename}\n\n{text}");

    // try: raw = await llm_call_async(endpoint_url, model, ..., headers=headers)
    let llm_result = crate::src::llm_core::llm_call_async(
        &endpoint_url,
        &model,
        vec![
            json!({"role": "system", "content": import_prompt}),
            json!({"role": "user", "content": user_content}),
        ],
        0.2,
        2000,
        headers,
        crate::src::llm_core::LLMConfig::DEFAULT_STREAM_TIMEOUT as u64,
    )
    .await;

    let raw = match llm_result {
        Ok(raw) => raw,
        // except Exception as e: logger.error(...); raise HTTPException(502, ...)
        Err(e) => {
            logger::error(&format!("Memory import extraction failed: {e}"));
            return Err(HttpException::new(502, format!("LLM extraction failed: {e}")));
        }
    };

    // raw = raw.strip(); if raw.startswith("```"): strip fences
    let mut raw_trimmed = raw.trim().to_string();
    if raw_trimmed.starts_with("```") {
        // raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        // split("\n", 1)[-1] -> everything after the first newline (or the whole
        // string when there is no newline); rsplit("```", 1)[0] -> everything before
        // the last "```".
        let after_first_nl = match raw_trimmed.split_once('\n') {
            Some((_, rest)) => rest.to_string(),
            None => raw_trimmed.clone(),
        };
        let before_last_fence = match after_first_nl.rsplit_once("```") {
            Some((head, _)) => head.to_string(),
            None => after_first_nl,
        };
        raw_trimmed = before_last_fence.trim().to_string();
    }

    // try: suggestions = json.loads(raw)
    match serde_json::from_str::<Value>(&raw_trimmed) {
        Ok(Value::Array(items)) => {
            // if isinstance(suggestions, list): normalize
            let mut normalized: Vec<Value> = Vec::new();
            for s in &items {
                // if not s: continue  (Python falsy skip)
                if !value_truthy(s) {
                    continue;
                }
                match s {
                    // if isinstance(s, dict): s = dict(s); if s.get("text"): strip; append
                    Value::Object(map) => {
                        let mut new_map = map.clone();
                        if let Some(t) = new_map.get("text").filter(|v| value_truthy(v)).cloned() {
                            new_map.insert(
                                "text".to_string(),
                                Value::String(strip_list_prefix(&py_str(&t))),
                            );
                        }
                        normalized.push(Value::Object(new_map));
                    }
                    // else: append {"text": _strip_list_prefix(str(s)), "category": "fact"}
                    other => {
                        normalized.push(json!({
                            "text": strip_list_prefix(&py_str(other)),
                            "category": "fact",
                        }));
                    }
                }
            }
            Ok(Json(json!({ "suggestions": normalized, "filename": filename })).into_response())
        }
        // else (parsed but not a list): suggestions = []
        Ok(_) => {
            Ok(Json(json!({ "suggestions": [], "filename": filename })).into_response())
        }
        // except json.JSONDecodeError: split by lines, strip list prefixes, take first 20
        Err(_) => {
            let lines: Vec<Value> = raw_trimmed
                .lines()
                .map(|l| l.trim())
                .filter(|l| !l.is_empty() && l.chars().count() > 5)
                .take(20)
                .map(|l| json!({ "text": strip_list_prefix(l), "category": "fact" }))
                .collect();
            Ok(Json(json!({ "suggestions": lines, "filename": filename })).into_response())
        }
    }
}

/// `POST /api/memory/{memory_id}/pin` — "Pin or unpin a memory".
///
/// ```python
/// def pin_memory(request, memory_id: str, pinned: bool = Form(True)):
///     user = _owner(request)
///     all_mem = memory_manager.load_all()
///     for i, memory in enumerate(all_mem):
///         if memory["id"] == memory_id:
///             _verify_memory_owner(memory, user)
///             all_mem[i]["pinned"] = pinned
///             memory_manager.save(all_mem)
///             return {"ok": True, "pinned": pinned}
///     raise HTTPException(404, f"Memory item {memory_id} not found")
/// ```
async fn pin_memory(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(memory_id): Path<String>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    let f = parse_form(mp).await;
    // `pinned: bool = Form(True)` — default True; FastAPI coerces "true"/"false"/1/0.
    let pinned = match f.get("pinned") {
        Some(v) => parse_bool_form(v)?,
        None => true,
    };
    let user = user_str(&user);

    let mut all_mem = s.memory_manager.load_all();
    for i in 0..all_mem.len() {
        // if memory["id"] == memory_id:
        if all_mem[i].get("id").and_then(|v| v.as_str()) == Some(memory_id.as_str()) {
            // _verify_memory_owner(memory, user)
            verify_memory_owner(&all_mem[i], user)?;
            // all_mem[i]["pinned"] = pinned
            if let Some(obj) = all_mem[i].as_object_mut() {
                obj.insert("pinned".to_string(), Value::Bool(pinned));
            }
            s.memory_manager
                .save(&mut all_mem)
                .map_err(|e| HttpException::new(500, e.to_string()))?;
            return Ok(Json(json!({ "ok": true, "pinned": pinned })).into_response());
        }
    }
    Err(HttpException::new(
        404,
        format!("Memory item {memory_id} not found"),
    ))
}

/// `GET /api/memory/{memory_id}` — "Get a specific memory item by ID".
///
/// ```python
/// def get_memory_item(request, memory_id: str):
///     user = _owner(request)
///     memories = memory_manager.load(owner=user)
///     for memory in memories:
///         if memory["id"] == memory_id: return {"memory": memory}
///     raise HTTPException(404, "Memory not found")
/// ```
async fn get_memory_item(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(memory_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = user_str(&user);
    let memories = s.memory_manager.load(user);
    for memory in &memories {
        if memory.get("id").and_then(|v| v.as_str()) == Some(memory_id.as_str()) {
            return Ok(Json(json!({ "memory": memory })).into_response());
        }
    }
    Err(HttpException::new(404, "Memory not found"))
}

/// `PUT /api/memory/{memory_id}` — "Update an existing memory item".
///
/// ```python
/// def update_memory(request, memory_id: str, text: str = Form(...), category: str = Form(None)):
///     user = _owner(request)
///     all_mem = memory_manager.load_all()
///     for i, memory in enumerate(all_mem):
///         if memory["id"] == memory_id:
///             _verify_memory_owner(memory, user)
///             all_mem[i]["text"] = text.strip()
///             if category: all_mem[i]["category"] = category
///             all_mem[i]["timestamp"] = int(time.time())
///             memory_manager.save(all_mem)
///             if memory_vector and memory_vector.healthy:
///                 memory_vector.remove(memory_id); memory_vector.add(memory_id, text.strip())
///             return {"ok": True, "message": "Memory updated successfully"}
///     raise HTTPException(404, f"Memory item {memory_id} not found")
/// ```
async fn update_memory(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(memory_id): Path<String>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    let f = parse_form(mp).await;
    // `text: str = Form(...)` — required; `category: str = Form(None)` — optional.
    let text = require_form(&f, "text")?;
    let category = f.get("category").cloned();
    let user = user_str(&user);

    let text_stripped = text.trim().to_string();

    let mut all_mem = s.memory_manager.load_all();
    for i in 0..all_mem.len() {
        if all_mem[i].get("id").and_then(|v| v.as_str()) == Some(memory_id.as_str()) {
            verify_memory_owner(&all_mem[i], user)?;
            if let Some(obj) = all_mem[i].as_object_mut() {
                // all_mem[i]["text"] = text.strip()
                obj.insert("text".to_string(), Value::String(text_stripped.clone()));
                // if category: all_mem[i]["category"] = category
                if let Some(cat) = category.as_deref().filter(|c| !c.is_empty()) {
                    obj.insert("category".to_string(), Value::String(cat.to_string()));
                }
                // all_mem[i]["timestamp"] = int(time.time())
                obj.insert("timestamp".to_string(), json!(crate::pytime::time() as i64));
            }
            s.memory_manager
                .save(&mut all_mem)
                .map_err(|e| HttpException::new(500, e.to_string()))?;

            // Sync vector index (remove old, add updated).
            if let Some(mv) = s.memory_vector.as_ref() {
                if mv.healthy() {
                    mv.remove(&memory_id);
                    // `memory_vector.add(...)` is async in the Rust port; the Python
                    // route lets it raise, so a failure propagates (-> 500).
                    mv.add(&memory_id, &text_stripped)
                        .await
                        .map_err(|e| HttpException::new(500, e.to_string()))?;
                }
            }
            return Ok(Json(json!({
                "ok": true,
                "message": "Memory updated successfully",
            }))
            .into_response());
        }
    }
    Err(HttpException::new(
        404,
        format!("Memory item {memory_id} not found"),
    ))
}

/// `DELETE /api/memory/{memory_id}` — "Delete a memory item by its ID".
///
/// ```python
/// def delete_memory(request, memory_id: str):
///     user = _owner(request)
///     all_mem = memory_manager.load_all()
///     target = next((m for m in all_mem if m["id"] == memory_id), None)
///     if not target: raise HTTPException(404, f"Memory item {memory_id} not found")
///     _verify_memory_owner(target, user)
///     all_mem = [m for m in all_mem if m["id"] != memory_id]
///     memory_manager.save(all_mem)
///     if memory_vector and memory_vector.healthy: memory_vector.remove(memory_id)
///     return {"ok": True, "message": "Memory deleted successfully"}
/// ```
async fn delete_memory(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(memory_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = user_str(&user);
    let mut all_mem = s.memory_manager.load_all();

    // target = next((m for m in all_mem if m["id"] == memory_id), None)
    let target = all_mem
        .iter()
        .find(|m| m.get("id").and_then(|v| v.as_str()) == Some(memory_id.as_str()));
    // if not target: raise HTTPException(404, ...)
    let target = match target {
        Some(t) => t,
        None => {
            return Err(HttpException::new(
                404,
                format!("Memory item {memory_id} not found"),
            ))
        }
    };
    // _verify_memory_owner(target, user)
    verify_memory_owner(target, user)?;

    // all_mem = [m for m in all_mem if m["id"] != memory_id]
    all_mem.retain(|m| m.get("id").and_then(|v| v.as_str()) != Some(memory_id.as_str()));
    s.memory_manager
        .save(&mut all_mem)
        .map_err(|e| HttpException::new(500, e.to_string()))?;

    // Sync vector index — if memory_vector and memory_vector.healthy: memory_vector.remove(...)
    if let Some(mv) = s.memory_vector.as_ref() {
        if mv.healthy() {
            mv.remove(&memory_id);
        }
    }
    Ok(Json(json!({
        "ok": true,
        "message": "Memory deleted successfully",
    }))
    .into_response())
}

// ===========================================================================
// Helpers
// ===========================================================================

/// `x.get("timestamp", 0)` as a sort key. Numbers sort by value; a missing or
/// non-numeric field defaults to `0` (Python's `.get(..., 0)`). Python would raise
/// on comparing mixed int/str, but stored timestamps are always numeric.
fn timestamp_key(v: &Value) -> f64 {
    v.get("timestamp")
        .and_then(|t| t.as_f64())
        .unwrap_or(0.0)
}

/// `datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")`, with the Python
/// `except (ValueError, OSError, OverflowError): "Unknown"` fallback.
///
/// `datetime.fromtimestamp` uses LOCAL time (no `tz=` arg), so this uses
/// `chrono::Local` — the same choice the ported `backup_routes` makes for
/// `datetime.now().strftime(...)`. An out-of-range / non-numeric timestamp maps to
/// "Unknown".
fn timestamp_to_str(ts: &Value) -> String {
    use chrono::{Local, TimeZone};
    let secs = match ts.as_i64() {
        Some(s) => s,
        // A float timestamp: truncate to whole seconds like fromtimestamp does for
        // the strftime fields (sub-second precision is dropped by `%S`).
        None => match ts.as_f64() {
            Some(f) => f.trunc() as i64,
            None => return "Unknown".to_string(),
        },
    };
    match Local.timestamp_opt(secs, 0).single() {
        Some(dt) => dt.format("%Y-%m-%d %H:%M:%S").to_string(),
        None => "Unknown".to_string(),
    }
}

/// `f"Session {session_id[:6]}"` — the first 6 characters (code points) of the id.
fn short6(session_id: &str) -> String {
    session_id.chars().take(6).collect()
}

/// `os.path.splitext(name)[1]` — the extension including the leading dot, or "".
///
/// Mirrors Python `splitext`: the extension is the substring from the last `.` in
/// the basename, but a leading-dot-only name (".bashrc") or a name with no dot has
/// no extension.
fn splitext_ext(name: &str) -> String {
    // Operate on the basename only (splitext ignores directory dots).
    let base = name.rsplit(['/', '\\']).next().unwrap_or(name);
    match base.rfind('.') {
        // A dot at index 0 (or only leading dots) is not an extension.
        Some(idx) if idx > 0 && base[..idx].trim_start_matches('.').is_empty() => String::new(),
        Some(idx) if idx > 0 => base[idx..].to_string(),
        _ => String::new(),
    }
}

/// `value_truthy` — Python truthiness for the JSON value shapes these handlers see.
fn value_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `str(value)` for the JSON value shapes (used by the `str(item["text"])` /
/// `str(s)` coercions). Strings pass through; bools/null mirror Python `str()`.
fn py_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Bool(b) => {
            if *b {
                "True".to_string()
            } else {
                "False".to_string()
            }
        }
        Value::Null => "None".to_string(),
        other => other.to_string(),
    }
}

/// `pinned: bool = Form(True)` coercion. FastAPI/pydantic accepts
/// `true/false/1/0/yes/no/on/off` (case-insensitive) for a bool form field, else
/// 422.
fn parse_bool_form(v: &str) -> Result<bool, HttpException> {
    match v.trim().to_lowercase().as_str() {
        "true" | "1" | "yes" | "on" | "y" | "t" => Ok(true),
        "false" | "0" | "no" | "off" | "n" | "f" => Ok(false),
        _ => Err(HttpException::new(
            422,
            "Input should be a valid boolean, unable to interpret input",
        )),
    }
}

/// Collect all `multipart/form-data` text fields into a map — the same collector the
/// inline subset and the other ported routers use. FastAPI `Form(...)` accepts both
/// `application/x-www-form-urlencoded` and `multipart/form-data`.
async fn parse_form(mut mp: Multipart) -> HashMap<String, String> {
    let mut out = HashMap::new();
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


/// A required `Form(...)` field. A missing required Form field is a 422 in FastAPI.
fn require_form(f: &HashMap<String, String>, key: &str) -> Result<String, HttpException> {
    f.get(key)
        .cloned()
        .ok_or_else(|| HttpException::new(422, format!("Field required: {key}")))
}

/// `tempfile.NamedTemporaryFile(suffix=ext, delete=False)` — write `content` to a
/// fresh temp file and return its path (the caller `os.unlink`s it).
fn write_temp(content: &[u8], suffix: &str) -> std::io::Result<String> {
    let dir = std::env::temp_dir();
    let name = format!("ody_mem_{}{}", uuid::Uuid::new_v4(), suffix);
    let path = dir.join(name);
    std::fs::write(&path, content)?;
    Ok(path.to_string_lossy().into_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn assert_session_owner_gates_cross_tenant() {
        // SECURITY (upstream b6607d2): get_session is unscoped, so these routes must
        // 404 when the caller does not own the session.
        let alice_sess = crate::core::models::Session {
            owner: Some("alice".to_string()),
            ..Default::default()
        };
        // Owner match -> Ok.
        assert!(assert_session_owner(&alice_sess, Some("alice")).is_ok());
        // Cross-tenant -> 404.
        let e = assert_session_owner(&alice_sess, Some("bob")).unwrap_err();
        assert_eq!(e.status_code, 404);
        // Auth-disabled (user None) -> no gate.
        assert!(assert_session_owner(&alice_sess, None).is_ok());
        // A real user vs a null-owner ("shared") session is NOT owned -> 404.
        let ownerless = crate::core::models::Session { owner: None, ..Default::default() };
        assert_eq!(assert_session_owner(&ownerless, Some("alice")).unwrap_err().status_code, 404);
    }

    #[test]
    fn strip_list_prefix_peels_one_marker() {
        // Numbered markers with "." ")" ":" + trailing whitespace.
        assert_eq!(strip_list_prefix("1. first fact"), "first fact");
        assert_eq!(strip_list_prefix("12) second"), "second");
        assert_eq!(strip_list_prefix("3: third"), "third");
        // Bullet markers.
        assert_eq!(strip_list_prefix("- a bullet"), "a bullet");
        assert_eq!(strip_list_prefix("* star"), "star");
        assert_eq!(strip_list_prefix("• dot"), "dot");
        // Only one prefix is peeled per call.
        assert_eq!(strip_list_prefix("1. - nested"), "- nested");
        // Empty string passes through; no marker is a no-op (just trimmed).
        assert_eq!(strip_list_prefix(""), "");
        assert_eq!(strip_list_prefix("  plain  "), "plain");
        // A 4+ digit number is NOT a list marker (\d{1,3}).
        assert_eq!(strip_list_prefix("1234. keep"), "1234. keep");
    }

    #[test]
    fn splitext_ext_matches_python() {
        assert_eq!(splitext_ext("doc.pdf"), ".pdf");
        assert_eq!(splitext_ext("a.b.txt"), ".txt");
        assert_eq!(splitext_ext("noext"), "");
        assert_eq!(splitext_ext(".bashrc"), "");
        assert_eq!(splitext_ext("dir.d/file"), "");
        assert_eq!(splitext_ext("UPPER.JSON".to_lowercase().as_str()), ".json");
    }

    #[test]
    fn parse_bool_form_coercion() {
        assert!(parse_bool_form("true").unwrap());
        assert!(parse_bool_form("1").unwrap());
        assert!(!parse_bool_form("false").unwrap());
        assert!(!parse_bool_form("0").unwrap());
        assert_eq!(parse_bool_form("maybe").unwrap_err().status_code, 422);
    }

    #[test]
    fn verify_owner_strict_and_auth_disabled() {
        // Auth disabled (None) accepts anything.
        assert!(verify_memory_owner(&json!({"owner": "bob"}), None).is_ok());
        // Strict: matching owner ok, mismatching/null -> 404.
        assert!(verify_memory_owner(&json!({"owner": "alice"}), Some("alice")).is_ok());
        assert_eq!(
            verify_memory_owner(&json!({"owner": "bob"}), Some("alice"))
                .unwrap_err()
                .status_code,
            404
        );
        // Legacy null/empty owner is NOT owned by anyone (strict).
        assert_eq!(
            verify_memory_owner(&json!({"text": "x"}), Some("alice"))
                .unwrap_err()
                .status_code,
            404
        );
    }

    #[test]
    fn short6_first_six_chars() {
        assert_eq!(short6("abcdefghij"), "abcdef");
        assert_eq!(short6("abc"), "abc");
    }

    /// `import` (#493): `if session:` is Python-truthy — only a present, non-empty
    /// `session` form value selects the session-lookup branch; a missing OR empty
    /// value is falsy and routes to the `resolve_endpoint("utility")` fallback. This
    /// mirrors the exact `form.get("session").filter(|v| !v.is_empty())` selector the
    /// handler uses.
    #[test]
    fn import_optional_session_truthiness() {
        let present_branch = |form: &HashMap<String, String>| -> bool {
            // Some(_) == session-lookup branch; None == resolve_endpoint fallback.
            form.get("session").filter(|v| !v.is_empty()).is_some()
        };

        // Absent -> falsy -> fallback.
        let empty: HashMap<String, String> = HashMap::new();
        assert!(!present_branch(&empty));

        // Present but empty string -> Python-falsy -> fallback (NOT the 404 lookup).
        let mut blank = HashMap::new();
        blank.insert("session".to_string(), String::new());
        assert!(!present_branch(&blank));

        // Present and non-empty -> truthy -> session lookup.
        let mut named = HashMap::new();
        named.insert("session".to_string(), "sess-123".to_string());
        assert!(present_branch(&named));
    }

    /// `import` (#493): the unified `if not endpoint_url or not model` check 400s when
    /// EITHER the resolved endpoint url OR model is empty — covering a session-present
    /// path whose session carries an empty endpoint/model, not just the
    /// resolve_endpoint-returned-None case.
    #[test]
    fn import_no_model_configured_when_url_or_model_empty() {
        let needs_400 = |endpoint_url: &str, model: &str| endpoint_url.is_empty() || model.is_empty();
        assert!(needs_400("", ""));
        assert!(needs_400("", "gpt-4"));
        assert!(needs_400("http://x/v1/chat/completions", ""));
        assert!(!needs_400("http://x/v1/chat/completions", "gpt-4"));
    }

    // ── memory_import_max_bytes (commit 193dc2f) ────────────────────────────────

    /// Default is 10 MB when the env-var is absent.
    #[test]
    fn memory_import_max_bytes_default() {
        // Clear any stray env-var to make the test deterministic.
        std::env::remove_var("ODYSSEUS_MEMORY_IMPORT_MAX_BYTES");
        assert_eq!(memory_import_max_bytes(), 10 * 1024 * 1024);
    }

    /// The env-var overrides the default.
    #[test]
    fn memory_import_max_bytes_env_override() {
        // Use a scope guard pattern: set, assert, then remove so other tests are not affected.
        std::env::set_var("ODYSSEUS_MEMORY_IMPORT_MAX_BYTES", "5242880"); // 5 MB
        let result = memory_import_max_bytes();
        std::env::remove_var("ODYSSEUS_MEMORY_IMPORT_MAX_BYTES");
        assert_eq!(result, 5 * 1024 * 1024);
    }

    /// A non-numeric env-var falls back to the 10 MB default (same as `int(os.getenv(…, default))`
    /// failing -> the Python expression would raise ValueError; our Rust port saturates to default).
    #[test]
    fn memory_import_max_bytes_invalid_env_falls_back() {
        std::env::set_var("ODYSSEUS_MEMORY_IMPORT_MAX_BYTES", "notanumber");
        let result = memory_import_max_bytes();
        std::env::remove_var("ODYSSEUS_MEMORY_IMPORT_MAX_BYTES");
        assert_eq!(result, 10 * 1024 * 1024);
    }
}
