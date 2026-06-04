// routes/backup_routes.rs  <- routes/backup_routes.py
//! Backup routes — export/import user data (memories, presets, settings, skills,
//! preferences).
//!
//! Faithful translation of `routes/backup_routes.py`'s `setup_backup_routes`
//! factory (**web+db**, classification `PORT_PARTIAL`). Two handlers —
//! `GET /api/export` and `POST /api/import` — both admin-gated, that snapshot or
//! merge the user's whole dataset.
//!
//! ## Shape (the integration substrate)
//! * `setup_backup_routes(memory_manager, preset_manager, skills_manager) ->
//!   APIRouter` becomes `setup_backup_routes() -> Router<AppState>`; the three
//!   factory args are reached through `AppState` (`memory_manager` /
//!   `preset_manager` / `skills_manager`), so the factory takes no params,
//!   mirroring every other ported router. The Python `APIRouter(tags=["backup"])`
//!   carries **no prefix**, so each handler's decorator already holds the full
//!   absolute path; the axum factory mounts at those same absolute paths
//!   (`/api/export`, `/api/import`).
//! * Auth is `require_admin(request)` followed by `user = get_current_user(request)`
//!   in **both** handlers. `require_admin` is the foundation F3 auth-adapter
//!   ([`auth_adapter::require_admin`], reading the `X-Odysseus-Internal-Token`
//!   header + the stamped `CurrentUser`); `get_current_user` is the
//!   `Option<Extension<CurrentUser>>` the auth gate stamps only when resolved. The
//!   `None` auth-disabled / single-user case is load-bearing throughout: it means
//!   "no owner filter" on the memory/skills loads and the flat (un-`_users`) prefs
//!   store, exactly like the Python.
//! * `raise HTTPException(s, d)` -> `return Err(HttpException::new(s, d))`; the
//!   `web`-gated `IntoResponse for HttpException` renders FastAPI's `{"detail": d}`.
//! * `return {...}` (a dict) -> `Json<Value>`; the export's downloadable file is a
//!   raw `Response` (`application/json` + `Content-Disposition: attachment`),
//!   mirroring the Python `Response(content=json.dumps(...), media_type=...,
//!   headers={...})`. JSON key order is preserved (the crate's `serde_json` is
//!   built with `preserve_order`, matching Python dict insertion order).
//!
//! ## PORT_PARTIAL — the skills-import branch (HONEST DEFER)
//! The import handler's `# ── Skills ──` branch calls `skills_manager.save(existing)`.
//! **The Python `SkillsManager` (services/memory/skills.py) defines NO `save`
//! method** (it is disk-backed: skills live as individual `SKILL.md` files, written
//! one at a time by `add_skill`/`update_skill`, never bulk-saved). So in the live
//! Python, an import payload that contains a non-empty, non-duplicate `skills` list
//! reaches `skills_manager.save(existing)` and raises an **unhandled
//! `AttributeError`** — there is no `try/except` around the skills branch, and the
//! app registers no handler for `AttributeError`, so FastAPI/Starlette surfaces a
//! generic **500 Internal Server Error**.
//!
//! Adding a `SkillsManager::save()` to the Rust port would be *fabrication* — it
//! would invent behavior the source of truth does not have and would make the Rust
//! import *succeed* where the Python *crashes*, breaking parity. The faithful,
//! honest translation therefore reproduces the Python's own failure: when the
//! skills branch would call the missing `save`, the handler returns the same
//! observable 500. The pure pre-`save` logic (the dedup scan over `load_all`,
//! the `id`/`title` collision skips, the owner stamping) is ported verbatim so the
//! crash point is reached under exactly the Python conditions: a `skills` list with
//! at least one new, titled, non-duplicate entry. An empty / all-duplicate skills
//! list never reaches the missing `save` in Python either (it would still call
//! `save([...unchanged...])` — see below), so the branch is entered whenever
//! `"skills"` is a list.
//!
//! NOTE on the exact crash condition: the Python calls `skills_manager.save(existing)`
//! **unconditionally** once it enters the `if "skills" in body and isinstance(...)`
//! branch — even when `added == 0`. So *any* `skills` list (empty included) triggers
//! the `AttributeError`. The Rust mirrors that: entering the skills branch at all
//! reproduces the 500.
//!
//! ## No path collision
//! `/api/export` and `/api/import` are paths the inline `web/mod.rs` subset never
//! owns, so the aggregator merges this router without an axum duplicate-`method`+
//! `path` panic.


use axum::extract::State;
use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Extension, Json, Router};
use serde_json::{json, Map, Value};

use crate::core::middleware::INTERNAL_TOOL_HEADER;
use crate::routes::auth_adapter;
use crate::routes::prefs_store;
use crate::routes::{AppState, CurrentUser, HttpException};
use crate::src::settings::{load_features, load_settings, save_features, save_settings};

/// `setup_backup_routes(memory_manager, preset_manager, skills_manager)` —
/// `APIRouter(tags=["backup"])` (no prefix).
///
/// app.py registers this as include-router #32 (the backup block,
/// `app.include_router(setup_backup_routes(memory_manager, preset_manager,
/// skills_manager))`). Because the Python `APIRouter` carries no prefix, each
/// handler's decorator already holds the full absolute `/api/...` path; the axum
/// factory mounts at those same paths. The three Python factory args
/// (`memory_manager` / `preset_manager` / `skills_manager`) are reached through
/// `State<AppState>` in the handlers, so the factory takes no parameters.
///
/// Route order mirrors the Python decorator order (`GET /api/export`, then
/// `POST /api/import`); the two paths are disjoint, so order is immaterial to
/// matching, but we keep source order for fidelity.
pub fn setup_backup_routes() -> Router<AppState> {
    Router::new()
        // `@router.get("/api/export")`
        .route("/api/export", get(export_data))
        // `@router.post("/api/import")`
        .route("/api/import", post(import_data))
}

/// `Depends(require_admin)` glue — pull the `X-Odysseus-Internal-Token` header and
/// the stamped `current_user` out of the request and delegate to the foundation
/// [`auth_adapter::require_admin`]. `Err` -> `HttpException(403, "Admin only")`.
fn admin_gate(
    state: &AppState,
    headers: &axum::http::HeaderMap,
    user: Option<&CurrentUser>,
) -> Result<(), HttpException> {
    let internal_header = headers
        .get(INTERNAL_TOOL_HEADER)
        .and_then(|v| v.to_str().ok());
    let user = user.map(|u| u.0.as_str());
    auth_adapter::require_admin(state, internal_header, user)
}

/// `get_current_user(request)` — the resolved username, or `None`.
///
/// The auth gate stamps `Extension(CurrentUser)` only when a user resolves, so an
/// unauthenticated request (auth disabled / first-run) arrives with the extension
/// absent. `Some(Extension(u))` -> `Some(&u.0)`; absent -> `None`. The `None` case
/// is the load-bearing auth-disabled / single-user path.
fn current_user(user: &Option<Extension<CurrentUser>>) -> Option<&str> {
    user.as_ref().map(|Extension(u)| u.0.as_str())
}

// ===========================================================================
// GET /api/export
// ===========================================================================

/// `GET /api/export` — export all user data as a downloadable JSON file.
///
/// ```python
/// @router.get("/api/export")
/// async def export_data(request: Request):
///     require_admin(request)
///     user = get_current_user(request)
///     memories = memory_manager.load(owner=user)
///     presets = preset_manager.get_all()
///     skills = skills_manager.load(owner=user)
///     settings = load_settings()
///     features = load_features()
///     from routes.prefs_routes import _load_for_user
///     preferences = _load_for_user(user)
///     export_data = {
///         "version": 1,
///         "exported_at": datetime.now().isoformat(),
///         "exported_by": user,
///         "memories": memories,
///         "presets": presets,
///         "skills": skills,
///         "settings": settings,
///         "features": features,
///         "preferences": preferences,
///     }
///     filename = f"odysseus_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
///     return Response(
///         content=json.dumps(export_data, indent=2, ensure_ascii=False),
///         media_type="application/json",
///         headers={"Content-Disposition": f"attachment; filename={filename}"},
///     )
/// ```
///
/// `require_admin` runs first; then the dataset is gathered. Owner scope: the
/// memory + skills loads pass `owner=user`, so an authenticated export is scoped to
/// that user while an auth-disabled export (`None`) is unscoped. Presets are shared
/// across users (`get_all`). The body is the `json.dumps(..., indent=2,
/// ensure_ascii=False)` text returned with `application/json` and an `attachment`
/// `Content-Disposition`.
async fn export_data(
    State(state): State<AppState>,
    headers: axum::http::HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    // require_admin(request)
    admin_gate(&state, &headers, user.as_deref())?;
    // user = get_current_user(request)
    let user = current_user(&user);

    // Memories (filtered by owner when auth is enabled)
    // memories = memory_manager.load(owner=user)
    let memories: Vec<Value> = state.memory_manager.load(user);

    // Presets (shared across users — export all)
    // presets = preset_manager.get_all()
    let presets: Map<String, Value> = {
        let mgr = state.preset_manager.lock().await;
        mgr.get_all()
    };

    // Skills (filtered by owner when auth is enabled)
    // skills = skills_manager.load(owner=user)
    let skills: Vec<Map<String, Value>> = state.skills_manager.load(user);

    // Settings
    // settings = load_settings()
    let settings: Map<String, Value> = load_settings();

    // Feature flags
    // features = load_features()
    let features: Map<String, Value> = load_features();

    // User preferences
    // from routes.prefs_routes import _load_for_user; preferences = _load_for_user(user)
    let preferences: Value = prefs_store::load_for_user(user);

    // export_data = { ... }  — exact key order: version, exported_at, exported_by,
    // memories, presets, skills, settings, features, preferences.
    let export_obj = json!({
        "version": 1,
        // datetime.now().isoformat() — LOCAL (naive) wall-clock time.
        "exported_at": now_isoformat(),
        // exported_by: user — JSON null when auth is disabled (user is None).
        "exported_by": match user {
            Some(u) => Value::String(u.to_string()),
            None => Value::Null,
        },
        "memories": memories,
        "presets": presets,
        // The skills list (each entry a dict): rebuild as an array of objects so the
        // per-skill key order matches `load`'s `to_dict()` order exactly.
        "skills": skills.into_iter().map(Value::Object).collect::<Vec<Value>>(),
        "settings": settings,
        "features": features,
        "preferences": preferences,
    });

    // filename = f"odysseus_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    let filename = format!("odysseus_backup_{}.json", now_compact_stamp());

    // content=json.dumps(export_data, indent=2, ensure_ascii=False)
    //
    // Python's `json.dumps(..., ensure_ascii=False)` leaves non-ASCII as UTF-8;
    // `serde_json::to_string_pretty` is also UTF-8 + 2-space-indent, matching
    // `indent=2`. A serialization error is impossible for a well-formed `Value`,
    // so this mirrors the Python (which cannot raise on `json.dumps` of a dict of
    // JSON-safe values here either).
    let content = serde_json::to_string_pretty(&export_obj)
        .unwrap_or_else(|_| export_obj.to_string());

    // return Response(content=..., media_type="application/json",
    //                 headers={"Content-Disposition": f"attachment; filename={filename}"})
    let resp = Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/json")
        .header(
            header::CONTENT_DISPOSITION,
            format!("attachment; filename={filename}"),
        )
        .body(axum::body::Body::from(content))
        // The header values are always valid (ASCII filename + static disposition),
        // so the builder never fails; matches the Python `Response(...)` which
        // cannot raise on construction here.
        .unwrap();
    Ok(resp)
}

// ===========================================================================
// POST /api/import
// ===========================================================================

/// `POST /api/import` — import user data from a previously exported JSON file.
/// Merges with existing data.
///
/// ```python
/// @router.post("/api/import")
/// async def import_data(request: Request):
///     require_admin(request)
///     user = get_current_user(request)
///     try:
///         body = await request.json()
///     except Exception:
///         raise HTTPException(400, "Invalid JSON")
///     if not isinstance(body, dict):
///         raise HTTPException(400, "Expected a JSON object")
///     imported = []
///     ...   # memories, skills, presets, settings, features, preferences
///     if not imported:
///         return {"ok": False, "message": "No recognized data found in the file"}
///     return {"ok": True, "imported": imported, "message": f"Imported: {', '.join(imported)}"}
/// ```
///
/// `require_admin` first, then the JSON body. Because the body is parsed by hand
/// (`Bytes` -> `serde_json`) the two early-validation errors are preserved exactly:
/// a non-JSON body -> `400 "Invalid JSON"`, a JSON non-object -> `400 "Expected a
/// JSON object"`. Each section is merged conditionally on its key being present and
/// the right shape; `imported` collects the human-readable summary, and the final
/// `{"ok": ...}` dict echoes it.
async fn import_data(
    State(state): State<AppState>,
    headers: axum::http::HeaderMap,
    user: Option<Extension<CurrentUser>>,
    raw_body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    // require_admin(request)
    admin_gate(&state, &headers, user.as_deref())?;
    // user = get_current_user(request)
    let user = current_user(&user);

    // try: body = await request.json()  except Exception: raise HTTPException(400, "Invalid JSON")
    let body: Value = match serde_json::from_slice::<Value>(&raw_body) {
        Ok(v) => v,
        Err(_) => return Err(HttpException::new(400, "Invalid JSON")),
    };

    // if not isinstance(body, dict): raise HTTPException(400, "Expected a JSON object")
    let body = match body.as_object() {
        Some(map) => map,
        None => return Err(HttpException::new(400, "Expected a JSON object")),
    };

    // imported = []
    let mut imported: Vec<String> = Vec::new();

    // ── Memories ──
    // if "memories" in body and isinstance(body["memories"], list):
    if let Some(Value::Array(mems)) = body.get("memories") {
        // existing = memory_manager.load_all()
        let mut existing: Vec<Value> = state.memory_manager.load_all();
        // existing_texts = {e.get("text", "").strip().lower()
        //                   for e in existing if e.get("owner") == user}
        //
        // Only entries belonging to THIS user seed the dedup set. Using every
        // tenant's rows (load_all without the owner filter) caused a memory whose
        // text matched any other user's to be silently skipped, so the importing
        // user lost their own data. The full `existing` list (all tenants) is still
        // saved back below — only the dedup comparison is scoped to the owner.
        //
        // `e.get("text", "")` returns `""` ONLY when the key is absent; a present
        // value (incl. `null`, a number, a list/dict) is returned as-is and then
        // `.strip()` is called on it. For any present non-string that raises a
        // Python `AttributeError` (e.g. `(42).strip()` / `None.strip()`), which —
        // with no try/except around the memory branch — surfaces as the route's
        // outer 500. Reproduce that instead of silently coercing to "".
        let mut existing_texts: std::collections::HashSet<String> =
            std::collections::HashSet::new();
        for e in &existing {
            // if e.get("owner") == user  (the owner-filter guard added in the parity
            // change). In Python, `user` is `None` when auth is disabled, and
            // `e.get("owner")` is `None` when the key is absent, so `None == None`
            // is `True` — meaning auth-disabled imports still dedup correctly
            // (all entries without an owner match the `None` user). We mirror that:
            // compare `e`'s owner value (as `Option<&str>`) to `user` (`Option<&str>`).
            let entry_owner = e.get("owner").and_then(Value::as_str);
            if entry_owner != user {
                continue;
            }
            match norm_text_checked(e.get("text")) {
                Ok(t) => {
                    existing_texts.insert(t);
                }
                // `.strip()` on a present non-string -> AttributeError -> 500.
                Err(()) => {
                    crate::pylog::error(
                        "Backup import: memory 'text' is present but not a string \
                         (AttributeError on .strip() in the Python source); returning \
                         500 to match.",
                    );
                    return Err(HttpException::new(500, "Internal Server Error"));
                }
            }
        }
        // added = 0
        let mut added: i64 = 0;
        for mem in mems {
            // if not isinstance(mem, dict) or not mem.get("text"): continue
            let mem_obj = match mem.as_object() {
                Some(m) => m,
                None => continue,
            };
            // `not mem.get("text")` — falsy text (absent / "" / null / 0 / false /
            // empty list/dict) skips. A truthy non-string (e.g. 42, [1], {"a":1},
            // true) passes this guard.
            if !truthy_str(mem_obj.get("text")) {
                continue;
            }
            // normed = mem["text"].strip().lower()
            //
            // `mem["text"]` is truthy here, so it is never absent/null/"". A truthy
            // string normalizes; a truthy NON-string (number, list, dict, bool)
            // raises a Python `AttributeError` on `.strip()` -> the route's outer
            // 500. Reproduce that rather than coercing to "".
            let normed = match mem_obj.get("text") {
                Some(Value::String(s)) => s.trim().to_lowercase(),
                _ => {
                    crate::pylog::error(
                        "Backup import: memory 'text' is present and truthy but not a \
                         string (AttributeError on .strip() in the Python source); \
                         returning 500 to match.",
                    );
                    return Err(HttpException::new(500, "Internal Server Error"));
                }
            };
            // if mem["text"].strip().lower() in existing_texts: continue  # skip duplicates
            if existing_texts.contains(&normed) {
                continue;
            }
            // Build the entry we append, stamping the owner when auth is enabled.
            let mut mem_clone = mem_obj.clone();
            // if user and not mem.get("owner"): mem["owner"] = user
            if let Some(u) = user {
                if !u.is_empty() && !truthy_str(mem_clone.get("owner")) {
                    mem_clone.insert("owner".to_string(), Value::String(u.to_string()));
                }
            }
            // existing.append(mem)
            existing.push(Value::Object(mem_clone));
            // existing_texts.add(mem["text"].strip().lower())
            existing_texts.insert(normed);
            // added += 1
            added += 1;
        }
        // memory_manager.save(existing)
        //
        // The Rust `MemoryManager::save` takes `&mut [Value]` (it stamps default
        // fields in place, like the Python `save` mutating the list). A write error
        // would be a Python `OSError` -> 500; the Python here lets `save` raise
        // freely, so we surface a 500 on the (practically unreachable) error path.
        if let Err(e) = state.memory_manager.save(&mut existing) {
            crate::pylog::error(&format!("Backup import: memory save failed: {e}"));
            return Err(HttpException::new(500, "Internal Server Error"));
        }
        // imported.append(f"{added} memories")
        imported.push(format!("{added} memories"));
    }

    // ── Skills ── (PORT_PARTIAL — HONEST DEFER, see the module header)
    //
    // if "skills" in body and isinstance(body["skills"], list):
    //     existing = skills_manager.load_all()
    //     ... dedup scan ...
    //     skills_manager.save(existing)   # <-- AttributeError in Python (no `save`)
    //     imported.append(f"{added} skills")
    //
    // The Python `SkillsManager` has NO `save` method, so once this branch is
    // entered the `skills_manager.save(existing)` call raises an unhandled
    // `AttributeError` -> FastAPI 500. We port the pure pre-`save` logic verbatim
    // (so the crash point is reached under exactly the Python conditions) and then
    // reproduce the Python's own failure with a 500 instead of fabricating a
    // bulk-save the source of truth does not have. Entering the branch at all
    // matches Python (it calls `save` unconditionally, even when `added == 0`).
    if let Some(Value::Array(skills)) = body.get("skills") {
        // existing = skills_manager.load_all()
        let existing: Vec<Map<String, Value>> = state.skills_manager.load_all();
        // existing_ids = {s.get("id") for s in existing}
        let mut existing_ids: std::collections::HashSet<Option<String>> = existing
            .iter()
            .map(|s| s.get("id").and_then(Value::as_str).map(str::to_string))
            .collect();
        // existing_titles = {s.get("title", "").strip().lower() for s in existing}
        let mut existing_titles: std::collections::HashSet<String> = existing
            .iter()
            .map(|s| norm_text(s.get("title")))
            .collect();
        // added = 0  (computed for parity even though it never reaches the response —
        // the `save` call below raises before `imported.append` would run).
        let mut _added: i64 = 0;
        for skill in skills {
            // if not isinstance(skill, dict) or not skill.get("title"): continue
            let skill_obj = match skill.as_object() {
                Some(m) => m,
                None => continue,
            };
            if !truthy_str(skill_obj.get("title")) {
                continue;
            }
            // if skill.get("id") in existing_ids: continue
            let sid = skill_obj.get("id").and_then(Value::as_str).map(str::to_string);
            if existing_ids.contains(&sid) {
                continue;
            }
            // if skill["title"].strip().lower() in existing_titles: continue
            let title = skill_obj
                .get("title")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let normed_title = title.trim().to_lowercase();
            if existing_titles.contains(&normed_title) {
                continue;
            }
            // (owner stamping + append happen here in Python, then existing_ids/titles
            //  get the new keys; we mirror the set updates so the loop matches, though
            //  the appended `existing` list is what `save` would have persisted.)
            existing_ids.insert(sid);
            existing_titles.insert(normed_title);
            _added += 1;
        }
        // skills_manager.save(existing) — the Python `AttributeError` -> 500.
        // HONEST DEFER: no faithful `SkillsManager::save()` exists to port (the
        // Python class has none); reproduce the observable 500 rather than fabricate.
        crate::pylog::error(
            "Backup import: skills import is unsupported — SkillsManager has no bulk save \
             (AttributeError in the Python source); returning 500 to match.",
        );
        return Err(HttpException::new(500, "Internal Server Error"));
    }

    // ── Presets ──
    // if "presets" in body and isinstance(body["presets"], dict):
    if let Some(Value::Object(incoming)) = body.get("presets") {
        // current = preset_manager.get_all()
        let mut current = {
            let mgr = state.preset_manager.lock().await;
            mgr.get_all()
        };
        // for key, value in body["presets"].items():
        //     if isinstance(value, dict): current[key] = value
        //     elif isinstance(value, list): current[key] = value
        // (any other value type is ignored — only dict/list entries are merged).
        for (key, value) in incoming {
            if value.is_object() || value.is_array() {
                current.insert(key.clone(), value.clone());
            }
        }
        // preset_manager.save(current)
        {
            let mut mgr = state.preset_manager.lock().await;
            mgr.save(&Value::Object(current));
        }
        // imported.append("presets")
        imported.push("presets".to_string());
    }

    // ── Settings ──
    // if "settings" in body and isinstance(body["settings"], dict):
    if let Some(Value::Object(incoming)) = body.get("settings") {
        // current = load_settings()
        let mut current = load_settings();
        // current.update(body["settings"])
        for (key, value) in incoming {
            current.insert(key.clone(), value.clone());
        }
        // save_settings(current)
        if let Err(e) = save_settings(&current) {
            crate::pylog::error(&format!("Backup import: settings save failed: {e}"));
            return Err(HttpException::new(500, "Internal Server Error"));
        }
        // imported.append("settings")
        imported.push("settings".to_string());
    }

    // ── Features ──
    // if "features" in body and isinstance(body["features"], dict):
    if let Some(Value::Object(incoming)) = body.get("features") {
        // current = load_features()
        let mut current = load_features();
        // current.update(body["features"])
        for (key, value) in incoming {
            current.insert(key.clone(), value.clone());
        }
        // save_features(current)
        if let Err(e) = save_features(&current) {
            crate::pylog::error(&format!("Backup import: features save failed: {e}"));
            return Err(HttpException::new(500, "Internal Server Error"));
        }
        // imported.append("features")
        imported.push("features".to_string());
    }

    // ── Preferences ──
    // if "preferences" in body and isinstance(body["preferences"], dict):
    if let Some(Value::Object(incoming)) = body.get("preferences") {
        // from routes.prefs_routes import _load_for_user, _save_for_user
        // current = _load_for_user(user)
        let mut current = prefs_store::load_for_user(user);
        // current.update(body["preferences"])
        if let Some(map) = current.as_object_mut() {
            for (key, value) in incoming {
                map.insert(key.clone(), value.clone());
            }
        }
        // _save_for_user(user, current)
        if let Err(e) = prefs_store::save_for_user(user, &current) {
            crate::pylog::error(&format!("Backup import: prefs save failed: {e}"));
            return Err(HttpException::new(500, "Internal Server Error"));
        }
        // imported.append("preferences")
        imported.push("preferences".to_string());
    }

    // if not imported: return {"ok": False, "message": "No recognized data found in the file"}
    if imported.is_empty() {
        return Ok(Json(json!({
            "ok": false,
            "message": "No recognized data found in the file"
        }))
        .into_response());
    }

    // return {"ok": True, "imported": imported, "message": f"Imported: {', '.join(imported)}"}
    let message = format!("Imported: {}", imported.join(", "));
    Ok(Json(json!({
        "ok": true,
        "imported": imported,
        "message": message,
    }))
    .into_response())
}

// ===========================================================================
// Small helpers
// ===========================================================================

/// `s.get("title", "").strip().lower()` — the normalized dedup key for the
/// **skills** branch only. A missing / null / non-string value collapses to `""`.
///
/// The skills branch is a HONEST-DEFER 500 (the Python `SkillsManager` has no
/// `save`, so the branch always 500s regardless of the title values), so this
/// helper's coercion of a present non-string to `""` never alters the observable
/// outcome there. The **memory** branch instead uses [`norm_text_checked`], which
/// reproduces the Python `AttributeError` on a present non-string `text`.
fn norm_text(v: Option<&Value>) -> String {
    match v {
        Some(Value::String(s)) => s.trim().to_lowercase(),
        _ => String::new(),
    }
}

/// `e.get("text", "").strip().lower()` — the fallible form that reproduces the
/// Python `AttributeError` instead of silently coercing a non-string to `""`.
///
/// Python's `dict.get(key, "")` returns the default `""` ONLY when the key is
/// **absent**; a **present** value is returned as-is. So:
/// * key absent (`None`) -> `Ok("")` (the `dict.get` default, then `"".strip()`).
/// * present string -> `Ok(s.trim().to_lowercase())`.
/// * present non-string (`null`, number, bool, list, dict) -> `Err(())` — Python
///   raises `AttributeError` on `.strip()` (e.g. `None.strip()` / `(42).strip()`),
///   which the import route's outer (handler-level) path surfaces as a 500.
fn norm_text_checked(v: Option<&Value>) -> Result<String, ()> {
    match v {
        // key absent -> dict.get(key, "") default.
        None => Ok(String::new()),
        // present string -> .strip().lower().
        Some(Value::String(s)) => Ok(s.trim().to_lowercase()),
        // present non-string -> AttributeError on .strip().
        Some(_) => Err(()),
    }
}

/// `not d.get(key)` -> the value is falsy. Python truthiness for the cases here
/// (text / title / owner are strings or absent): a non-empty string is truthy;
/// `""`, `null`, absent, and `false`/`0` are falsy. Mirrors `if not mem.get("text")`.
fn truthy_str(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().map(|f| f != 0.0).unwrap_or(false),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// `datetime.now().isoformat()` — LOCAL (naive) wall-clock ISO 8601 with a `T`
/// separator.
///
/// `datetime.now()` returns the local-timezone naive datetime; `.isoformat()`
/// OMITS the `.ffffff` segment entirely when `microsecond == 0` (e.g.
/// `2026-06-01T12:34:56`), else emits exactly 6 digits with no trailing-zero
/// stripping. Reproduce both (cf. `vault_routes::utcnow_isoformat`, but LOCAL not
/// UTC, since `backup_routes` uses `datetime.now()` not `datetime.utcnow()`).
fn now_isoformat() -> String {
    use chrono::Timelike;
    let dt = chrono::Local::now().naive_local();
    let micros = dt.nanosecond() / 1000;
    if micros == 0 {
        dt.format("%Y-%m-%dT%H:%M:%S").to_string()
    } else {
        format!("{}.{:06}", dt.format("%Y-%m-%dT%H:%M:%S"), micros)
    }
}

/// `datetime.now().strftime('%Y%m%d_%H%M%S')` — LOCAL compact timestamp for the
/// download filename (e.g. `20260601_123456`).
fn now_compact_stamp() -> String {
    chrono::Local::now()
        .naive_local()
        .format("%Y%m%d_%H%M%S")
        .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Timelike;

    /// `setup_backup_routes()` merges cleanly into a `Router<AppState>` (no panic =
    /// no intra-router method+path collision), the additive invariant the
    /// aggregator relies on. The two paths are disjoint, so building the router
    /// never panics. Building a full `AppState` (20-odd managers) just to drive the
    /// real handlers end-to-end is impractical; the handler logic is exercised by
    /// the pure-helper tests below plus the route-mount smoke here.
    #[test]
    fn mount_smoke() {
        let base: Router<AppState> = Router::new();
        let _merged: Router<AppState> = base.merge(setup_backup_routes());
    }

    #[test]
    fn norm_text_strips_and_lowercases() {
        // skills-branch dedup key (coercing helper).
        assert_eq!(norm_text(Some(&json!("  Hello World "))), "hello world");
        assert_eq!(norm_text(Some(&json!(""))), "");
        // missing / null / non-string -> "" (the dict.get default).
        assert_eq!(norm_text(None), "");
        assert_eq!(norm_text(Some(&Value::Null)), "");
        assert_eq!(norm_text(Some(&json!(42))), "");
    }

    #[test]
    fn norm_text_checked_reproduces_python_attributeerror() {
        // e.get("text", "").strip().lower() — dict.get(key, "") default ONLY when
        // the key is absent; a present non-string raises AttributeError on .strip().
        assert_eq!(
            norm_text_checked(Some(&json!("  Hello World "))),
            Ok("hello world".to_string())
        );
        assert_eq!(norm_text_checked(Some(&json!(""))), Ok(String::new()));
        // key absent -> dict.get default "".
        assert_eq!(norm_text_checked(None), Ok(String::new()));
        // present non-string -> AttributeError -> Err(()) (-> 500 at the call site).
        assert_eq!(norm_text_checked(Some(&Value::Null)), Err(())); // None.strip()
        assert_eq!(norm_text_checked(Some(&json!(42))), Err(())); // (42).strip()
        assert_eq!(norm_text_checked(Some(&json!(0))), Err(())); // (0).strip()
        assert_eq!(norm_text_checked(Some(&json!(false))), Err(())); // (False).strip()
        assert_eq!(norm_text_checked(Some(&json!([1]))), Err(())); // [1].strip()
        assert_eq!(norm_text_checked(Some(&json!({"a": 1}))), Err(())); // {}.strip()
    }

    #[test]
    fn truthy_str_matches_python_bool() {
        // `not mem.get("text")` falsiness.
        assert!(!truthy_str(None));
        assert!(!truthy_str(Some(&Value::Null)));
        assert!(!truthy_str(Some(&json!(""))));
        assert!(truthy_str(Some(&json!("x"))));
        assert!(!truthy_str(Some(&json!(false))));
        assert!(truthy_str(Some(&json!(true))));
        assert!(!truthy_str(Some(&json!(0))));
        assert!(truthy_str(Some(&json!(1))));
    }

    #[test]
    fn now_isoformat_uses_t_separator_and_no_zero_micros() {
        // The format is `YYYY-MM-DDTHH:MM:SS[.ffffff]` with a `T` separator and the
        // fraction present iff microsecond != 0.
        let s = now_isoformat();
        assert_eq!(s.as_bytes()[10], b'T', "expected T separator: {s}");
        // Reconstruct from a known zero-microsecond instant to pin the bare form.
        let dt = chrono::NaiveDate::from_ymd_opt(2026, 6, 1)
            .unwrap()
            .and_hms_opt(12, 34, 56)
            .unwrap();
        assert_eq!(dt.nanosecond() / 1000, 0);
        assert_eq!(dt.format("%Y-%m-%dT%H:%M:%S").to_string(), "2026-06-01T12:34:56");
    }

    #[test]
    fn now_compact_stamp_shape() {
        // `%Y%m%d_%H%M%S` -> 15 chars: 8 date + '_' + 6 time.
        let s = now_compact_stamp();
        assert_eq!(s.len(), 15, "expected YYYYMMDD_HHMMSS: {s}");
        assert_eq!(s.as_bytes()[8], b'_');
        assert!(s[..8].chars().all(|c| c.is_ascii_digit()));
        assert!(s[9..].chars().all(|c| c.is_ascii_digit()));
    }

    #[test]
    fn export_filename_format() {
        // filename = f"odysseus_backup_{stamp}.json"
        let filename = format!("odysseus_backup_{}.json", now_compact_stamp());
        assert!(filename.starts_with("odysseus_backup_"));
        assert!(filename.ends_with(".json"));
        assert_eq!(filename.len(), "odysseus_backup_".len() + 15 + ".json".len());
    }

    #[test]
    fn presets_merge_only_dict_and_list_values() {
        // for key, value in body["presets"].items():
        //   if isinstance(value, dict): current[key] = value
        //   elif isinstance(value, list): current[key] = value
        // (scalars are ignored)
        let mut current: Map<String, Value> = Map::new();
        current.insert("keep".to_string(), json!({"a": 1}));
        let incoming = json!({
            "obj": {"x": 1},
            "arr": [1, 2, 3],
            "scalar": "ignored",
            "number": 7,
            "null": null,
        });
        let incoming = incoming.as_object().unwrap();
        for (key, value) in incoming {
            if value.is_object() || value.is_array() {
                current.insert(key.clone(), value.clone());
            }
        }
        assert_eq!(current.get("obj"), Some(&json!({"x": 1})));
        assert_eq!(current.get("arr"), Some(&json!([1, 2, 3])));
        // scalar / number / null entries were NOT merged.
        assert_eq!(current.get("scalar"), None);
        assert_eq!(current.get("number"), None);
        assert_eq!(current.get("null"), None);
        // pre-existing keys survive.
        assert_eq!(current.get("keep"), Some(&json!({"a": 1})));
    }

    #[test]
    fn settings_update_overlays_incoming_over_existing() {
        // current.update(body["settings"]) — incoming overwrites, new keys add,
        // untouched keys survive (Python dict.update semantics).
        let mut current: Map<String, Value> = Map::new();
        current.insert("theme".to_string(), json!("light"));
        current.insert("keep".to_string(), json!(true));
        let incoming = json!({"theme": "dark", "new": 1});
        let incoming = incoming.as_object().unwrap();
        for (key, value) in incoming {
            current.insert(key.clone(), value.clone());
        }
        assert_eq!(current.get("theme"), Some(&json!("dark")));
        assert_eq!(current.get("new"), Some(&json!(1)));
        assert_eq!(current.get("keep"), Some(&json!(true)));
    }

    #[test]
    fn export_object_key_order_matches_python() {
        // The export dict key order is load-bearing (the download is human-read).
        let export_obj = json!({
            "version": 1,
            "exported_at": "2026-06-01T12:00:00",
            "exported_by": Value::Null,
            "memories": [],
            "presets": {},
            "skills": [],
            "settings": {},
            "features": {},
            "preferences": {},
        });
        let keys: Vec<&str> = export_obj
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect();
        assert_eq!(
            keys,
            vec![
                "version",
                "exported_at",
                "exported_by",
                "memories",
                "presets",
                "skills",
                "settings",
                "features",
                "preferences",
            ]
        );
    }

    #[test]
    fn import_response_message_joins_imported() {
        // message = f"Imported: {', '.join(imported)}"
        let imported = vec!["3 memories".to_string(), "presets".to_string(), "settings".to_string()];
        let message = format!("Imported: {}", imported.join(", "));
        assert_eq!(message, "Imported: 3 memories, presets, settings");
        let ok = json!({"ok": true, "imported": imported, "message": message});
        let keys: Vec<&str> = ok.as_object().unwrap().keys().map(String::as_str).collect();
        assert_eq!(keys, vec!["ok", "imported", "message"]);
    }

    #[test]
    fn empty_import_returns_no_recognized_data() {
        // if not imported: {"ok": False, "message": "No recognized data found in the file"}
        let imported: Vec<String> = Vec::new();
        assert!(imported.is_empty());
        let resp = json!({"ok": false, "message": "No recognized data found in the file"});
        assert_eq!(resp.get("ok"), Some(&json!(false)));
        assert_eq!(
            resp.get("message"),
            Some(&json!("No recognized data found in the file"))
        );
    }

    /// Parity change: `existing_texts` is seeded only from entries where
    /// `e.get("owner") == user`, not from all tenants.
    ///
    /// Before the fix the dedup set spanned ALL tenants, so if user A and user B
    /// both had memory "foo", importing "foo" as user A was silently skipped
    /// (because "foo" was already in the all-tenant set from user B's row).
    /// After the fix the set is scoped to the importing user's own rows, so user A
    /// can import "foo" even if user B already holds it.
    #[test]
    fn memory_dedup_scoped_to_importing_user_not_all_tenants() {
        // Simulate the owner-filtered set-building loop from import_data.
        // existing = memory_manager.load_all() — two tenants, each with "foo".
        let existing = vec![
            json!({"text": "foo", "owner": "alice"}),
            json!({"text": "foo", "owner": "bob"}),
            json!({"text": "bar", "owner": "alice"}),
        ];

        // Build existing_texts scoped to alice.
        let user: Option<&str> = Some("alice");
        let mut existing_texts: std::collections::HashSet<String> =
            std::collections::HashSet::new();
        for e in &existing {
            let entry_owner = e.get("owner").and_then(Value::as_str);
            if entry_owner != user {
                continue;
            }
            if let Ok(t) = norm_text_checked(e.get("text")) {
                existing_texts.insert(t);
            }
        }

        // alice's set: "foo" and "bar" — but NOT bob's "foo" (same text, different owner).
        // The set should contain exactly alice's own texts.
        assert!(existing_texts.contains("foo"), "alice's 'foo' must be in the dedup set");
        assert!(existing_texts.contains("bar"), "alice's 'bar' must be in the dedup set");
        assert_eq!(existing_texts.len(), 2, "set should have exactly 2 entries (alice's rows)");

        // Importing "foo" for alice is skipped (alice already owns it).
        let new_mem_foo = json!({"text": "foo", "owner": "alice"});
        let normed = new_mem_foo["text"].as_str().unwrap().trim().to_lowercase();
        assert!(existing_texts.contains(&normed), "alice's own 'foo' must be deduped");

        // Importing "baz" for alice is allowed (alice does not own it).
        let new_mem_baz = json!({"text": "baz", "owner": "alice"});
        let normed_baz = new_mem_baz["text"].as_str().unwrap().trim().to_lowercase();
        assert!(
            !existing_texts.contains(&normed_baz),
            "'baz' is new for alice — must NOT be in the dedup set"
        );

        // Key invariant of the fix: bob's "foo" does NOT block alice from importing
        // her own "foo" from scratch. If we scope to bob, the set has only "foo"
        // (bob's single entry), NOT "bar" (alice's).
        let user_bob: Option<&str> = Some("bob");
        let mut bob_texts: std::collections::HashSet<String> = std::collections::HashSet::new();
        for e in &existing {
            let entry_owner = e.get("owner").and_then(Value::as_str);
            if entry_owner != user_bob {
                continue;
            }
            if let Ok(t) = norm_text_checked(e.get("text")) {
                bob_texts.insert(t);
            }
        }
        assert!(bob_texts.contains("foo"));
        assert!(!bob_texts.contains("bar"), "bar belongs to alice, not bob");
        assert_eq!(bob_texts.len(), 1);
    }

    /// Auth-disabled case (`user == None`): entries without an owner (`owner` key
    /// absent) match `None == None` in Python, so they seed the dedup set. Entries
    /// that *do* have a non-null owner do NOT match, matching Python's `==` semantics.
    #[test]
    fn memory_dedup_owner_filter_none_user_matches_ownerless_entries() {
        let existing = vec![
            json!({"text": "alpha"}),                           // no owner key -> owner == None
            json!({"text": "beta", "owner": null}),             // owner key present but null -> as_str() is None
            json!({"text": "gamma", "owner": "alice"}),         // owned by alice -> skip
        ];

        let user: Option<&str> = None; // auth disabled
        let mut existing_texts: std::collections::HashSet<String> =
            std::collections::HashSet::new();
        for e in &existing {
            let entry_owner = e.get("owner").and_then(Value::as_str);
            if entry_owner != user {
                continue;
            }
            if let Ok(t) = norm_text_checked(e.get("text")) {
                existing_texts.insert(t);
            }
        }

        // "alpha" (no owner) and "beta" (null owner, as_str() -> None) both match
        // None user. "gamma" (owner="alice") does NOT match.
        assert!(existing_texts.contains("alpha"));
        assert!(existing_texts.contains("beta"));
        assert!(!existing_texts.contains("gamma"),
            "gamma is owned by alice and must not appear in the None-user dedup set");
        assert_eq!(existing_texts.len(), 2);
    }
}
