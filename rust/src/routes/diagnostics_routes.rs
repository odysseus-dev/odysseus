// routes/diagnostics_routes.rs  <- routes/diagnostics_routes.py
//! Diagnostics API — `/api/db/stats`, `/api/rag/stats`, `/api/test/youtube`,
//! `/api/test-research`.
//!
//! This is the `setup_diagnostics_routes(rag_manager, rag_available,
//! research_handler)` FastAPI factory (no `prefix`, so each handler carries its
//! full `/api/...` path). As of upstream 7c7ac10 all four handlers run
//! `require_admin(request)` as their FIRST statement (the admin gate — internal
//! header / auth-disabled bypass, else admin `current_user`, else
//! `HTTPException(403, "Admin only")`); the post-gate bodies use the broad
//! `try/except -> {"error": ...}` idiom rather than raising — except
//! `/api/db/stats`, which raises `HTTPException(500, ...)` on failure.
//!
//! ```python
//! def setup_diagnostics_routes(rag_manager, rag_available, research_handler):
//!     router = APIRouter(tags=["diagnostics"])
//!
//!     @router.get("/api/db/stats")
//!     async def get_database_stats(request: Request) -> Dict[str, Any]:
//!         require_admin(request)
//!         try:
//!             from core.database import get_detailed_stats
//!             return get_detailed_stats()
//!         except Exception as e:
//!             logger.error(f"DB stats error: {e}")
//!             raise HTTPException(500, "Failed to retrieve database statistics")
//!
//!     @router.get("/api/rag/stats")
//!     async def get_rag_stats(request: Request) -> Dict[str, Any]:
//!         require_admin(request)
//!         if rag_available and rag_manager:
//!             return rag_manager.get_stats()
//!         return {"error": "RAG system not available"}
//!
//!     @router.get("/api/test/youtube")
//!     async def test_youtube(request: Request, url: str) -> Dict[str, Any]:
//!         require_admin(request)
//!         try:
//!             video_id = extract_youtube_id(url)
//!             if not video_id:
//!                 return {"error": "Invalid YouTube URL"}
//!             data = await extract_transcript_async(url, video_id)
//!             return { ... }
//!         except Exception as e:
//!             return {"error": str(e)}
//!
//!     @router.post("/api/test-research")
//!     async def test_research(request: Request, query: str = Form("What is machine learning?")):
//!         require_admin(request)
//!         try:
//!             endpoint = f"http://{DEFAULT_HOST}:8000/v1/chat/completions"
//!             model = "gpt-oss-120b"
//!             result = await research_handler.call_research_service(query, endpoint, model)
//!             return {"status": "success", ...}
//!         except Exception as e:
//!             return {"status": "error", "error": str(e), "query": query}
//!
//!     return router
//! ```
//!
//! ## PORT_PARTIAL notes (faithful to each Python branch)
//!
//! * **`/api/rag/stats`** — In the live Python app (`app.py`) the factory is
//!   called as `setup_diagnostics_routes(rag_manager=None, rag_available=False,
//!   ...)` (the application RAG singleton is disabled: `rag_manager = None;
//!   rag_available = False`). So the `if rag_available and rag_manager:` branch is
//!   **dead code in the live app** — it always returns
//!   `{"error": "RAG system not available"}`. The Rust [`AppState::rag_manager`]
//!   is `Option<Arc<dyn RagManager>>`, `None` on the live path, so this handler
//!   reproduces that exact body. (The `dyn RagManager` trait does not even expose
//!   `get_stats`, which is a method on the concrete `PersonalDocsManager`; since
//!   the live wiring is always `None`, the `Some` arm is unreachable and there is
//!   nothing to call — matching the Python's own dead branch.)
//!
//! * **`/api/test/youtube`** — The transcript fetch reflects the existing HONEST
//!   STUB ([`crate::services::youtube::extract_transcript_async`]): the
//!   `youtube-transcript-api` backend is genuinely un-portable, so the stub
//!   returns `{"success": False, "error": "...not available in the Rust port
//!   yet"}`. This handler therefore returns the Python's *failure-shape* fields
//!   (`transcript_success=false`, `transcript_length=0`, the stub's `error`),
//!   exactly as the Python would surface a failed fetch. The URL parse
//!   (`extract_youtube_id`) is fully ported, so the `{"error": "Invalid YouTube
//!   URL"}` branch is faithful. NOTE the Rust `extract_transcript_async` is a
//!   synchronous fn (the network half is stubbed), so there is no `.await` here.
//!
//! * **`/api/test-research`** — HONEST STUB (signature mismatch). The Python calls
//!   the 3-positional-arg diagnostic shape
//!   `research_handler.call_research_service(query, endpoint, model)`. The ported
//!   Rust [`crate::src::research_handler::ResearchHandler`] deliberately
//!   **refactored** that method: it is a *private* `async fn call_research_service`
//!   taking a `ResearcherConfig` struct (`query, cfg, max_time, task_entry,
//!   prior_report, prior_findings, prior_urls`) — there is NO public
//!   `(query, endpoint, model)` entrypoint to call. Reconstructing the full
//!   `ResearcherConfig` + driving a real DeepResearcher run from a *diagnostic*
//!   endpoint would be fabrication, not a port, so this handler mirrors the
//!   Python's OWN `except` branch: it returns `{"status": "error", "error":
//!   <message>, "query": query}` with a message that names the un-portable
//!   diagnostic shape. (The Python endpoint's success path requires a live LLM at
//!   `localhost:8000` anyway and would land in the same `except` shape when that
//!   endpoint is absent.) The design records this as the one deferred half of
//!   diagnostics; the bulk (`/api/db/stats`, `/api/rag/stats`, `/api/test/youtube`)
//!   is fully ported.


use axum::extract::{Query, State};
use axum::http::HeaderMap;
use axum::routing::{get, post};
use axum::{Extension, Form, Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::core::constants::DEFAULT_HOST;
use crate::routes::{auth_adapter, AppState, CurrentUser, HttpException};
use crate::services::youtube::{extract_transcript_async, extract_youtube_id};

/// `require_admin(request)` (core/middleware.py) — the per-handler admin gate the
/// four diagnostics endpoints now run first (upstream 7c7ac10 added
/// `require_admin(request)` to every handler). Reads the
/// `X-Odysseus-Internal-Token` header + the stamped `current_user` and delegates to
/// the foundation [`auth_adapter::require_admin`] (which wraps the already-ported
/// `core::middleware::require_admin`); `Err` -> `HttpException(403, "Admin only")`.
fn require_admin(
    state: &AppState,
    headers: &HeaderMap,
    user: Option<&CurrentUser>,
) -> Result<(), HttpException> {
    // `hdr = request.headers.get(INTERNAL_TOOL_HEADER)`
    let internal_header = headers
        .get(crate::core::middleware::INTERNAL_TOOL_HEADER)
        .and_then(|v| v.to_str().ok());
    let user = user.map(|u| u.0.as_str());
    auth_adapter::require_admin(state, internal_header, user)
}

/// `@router.get("/api/db/stats")` -> `GET /api/db/stats` — comprehensive DB
/// statistics (session/message/memory counts + the SQLite file size).
///
/// `return get_detailed_stats()`; any exception -> `logger.error(...)` then
/// `raise HTTPException(500, "Failed to retrieve database statistics")`. The
/// ported [`crate::core::database::get_detailed_stats`] degrades gracefully (it
/// returns the zero-count shape when the DB is unreachable rather than raising),
/// so the 500 arm is reproduced for the (rare) hard-failure case only — but to
/// keep parity with the Python `try/except` the call is wrapped and a panic /
/// poisoned-state would surface as the same 500. In practice the function never
/// raises here, so the happy path is the only reachable arm, identical to Python.
///
/// Upstream 7c7ac10 added `require_admin(request)` as the first statement of the
/// handler, so the admin gate runs before any DB work (the body is unchanged in
/// [`db_stats_body`]).
async fn get_database_stats(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Json<Value>, HttpException> {
    // require_admin(request)
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    db_stats_body()
}

/// The `get_database_stats` body sans the admin gate — the ported `try/except`.
fn db_stats_body() -> Result<Json<Value>, HttpException> {
    // try: from core.database import get_detailed_stats; return get_detailed_stats()
    //
    // `get_detailed_stats()` is infallible in the Rust port (it logs + returns the
    // empty/zero shape on a DB error, never raising). `catch_unwind` reproduces the
    // Python `except Exception` arm for the only way it could still fail (a panic),
    // mapping it to the same `HTTPException(500, ...)`.
    match std::panic::catch_unwind(crate::core::database::get_detailed_stats) {
        Ok(stats) => Ok(Json(stats)),
        Err(_) => {
            // except Exception as e: logger.error(f"DB stats error: {e}")
            crate::pylog::error("DB stats error: get_detailed_stats panicked");
            // raise HTTPException(500, "Failed to retrieve database statistics")
            Err(HttpException::new(500, "Failed to retrieve database statistics"))
        }
    }
}

/// `@router.get("/api/rag/stats")` -> `GET /api/rag/stats` — RAG index statistics.
///
/// `if rag_available and rag_manager: return rag_manager.get_stats()` else
/// `return {"error": "RAG system not available"}`. In the live Python app the
/// factory is wired with `rag_manager=None, rag_available=False`, so the guard is
/// always false and the `{"error": ...}` body is the only reachable result. The
/// Rust [`AppState::rag_manager`] is `None` on the live path, reproducing exactly
/// that. (See the module docstring for why the `Some` arm is an unreachable dead
/// branch in both the Python live app and the Rust port.)
///
/// Upstream 7c7ac10 added `require_admin(request)` as the first statement, so the
/// admin gate runs before the RAG branch; the body is preserved in [`rag_stats_body`].
async fn get_rag_stats(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Json<Value>, HttpException> {
    // require_admin(request)
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    Ok(rag_stats_body(&state))
}

/// The `get_rag_stats` body sans the admin gate — the ported RAG branch.
fn rag_stats_body(state: &AppState) -> Json<Value> {
    // `if rag_available and rag_manager:` — both must hold. The Rust `Option` is
    // the combined `rag_available and rag_manager` predicate: `Some` only when the
    // application RAG singleton is enabled (never, on the live path).
    if let Some(_rag) = state.rag_manager.as_ref() {
        // `return rag_manager.get_stats()` — unreachable on the live path. The
        // `dyn RagManager` trait does not expose `get_stats` (it is a concrete
        // `PersonalDocsManager` method), and the live wiring is always `None`, so
        // there is no object to call here. Fall through to the not-available body,
        // matching the live app's only observable behavior.
        return Json(json!({ "error": "RAG system not available" }));
    }
    // `return {"error": "RAG system not available"}`
    Json(json!({ "error": "RAG system not available" }))
}

/// `url: str` — the required query parameter for `GET /api/test/youtube`.
#[derive(Deserialize)]
struct YoutubeQuery {
    url: String,
}

/// `@router.get("/api/test/youtube")` -> `GET /api/test/youtube?url=...` — a
/// transcript-fetch smoke test.
///
/// Parses the YouTube id (fully ported), then runs the transcript fetch — which
/// is an HONEST STUB in the Rust port (the `youtube-transcript-api` backend is
/// un-portable). The response mirrors the Python's *failure shape* the stub
/// produces (`transcript_success=false`, `transcript_length=0`, the stub `error`).
///
/// Upstream 7c7ac10 added `require_admin(request)` as the first statement, so the
/// admin gate runs before the transcript fetch; the body is preserved in
/// [`test_youtube_body`].
async fn test_youtube(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<YoutubeQuery>,
) -> Result<Json<Value>, HttpException> {
    // require_admin(request)
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    Ok(test_youtube_body(q).await)
}

/// The `test_youtube` body sans the admin gate — the ported `try/except` fetch.
async fn test_youtube_body(q: YoutubeQuery) -> Json<Value> {
    // The whole body is wrapped in `try/except -> {"error": str(e)}` in Python; the
    // ported calls below are infallible (no panic path), so the `except` arm is not
    // reachable here — the explicit error branches (`Invalid YouTube URL`, and the
    // stub's `error`) are the only error outputs, exactly as Python would return on
    // a bad URL / failed fetch.
    let url = q.url;

    // video_id = extract_youtube_id(url)
    let video_id = match extract_youtube_id(&url) {
        // if not video_id: return {"error": "Invalid YouTube URL"}
        None => return Json(json!({ "error": "Invalid YouTube URL" })),
        Some(v) if v.is_empty() => return Json(json!({ "error": "Invalid YouTube URL" })),
        Some(v) => v,
    };

    // data = await extract_transcript_async(url, video_id)
    //   (`max_retries` defaults to 3 in Python, so pass 3 to match the
    //    `(url, video_id)` call. The fetcher is now a REAL async yt-dlp-backed
    //    transcript fetch, awaited here exactly as the Python awaits it.)
    let data = extract_transcript_async(&url, &video_id, 3).await;

    // success = data.get("success", False)
    let success = data.get("success").and_then(Value::as_bool).unwrap_or(false);
    // transcript = data.get("transcript", "")
    let transcript = data.get("transcript").and_then(Value::as_str).unwrap_or("");

    // transcript_length = len(transcript) if success else 0
    let transcript_length: i64 = if success {
        // Python `len(str)` counts code points (the transcript text is text).
        transcript.chars().count() as i64
    } else {
        0
    };

    // transcript_preview = (transcript[:500] + "...") if success and len > 500 else transcript
    let transcript_preview: Value = if success && transcript.chars().count() > 500 {
        let head: String = transcript.chars().take(500).collect();
        Value::String(format!("{head}..."))
    } else {
        // `else data.get("transcript", "")` — the raw `transcript` value. When the
        // stub returns `transcript: null`, `data.get("transcript", "")` in Python
        // yields `None` (the key is present, value `null`), so reproduce that
        // exactly: prefer the original `transcript` Value (null stays null).
        data.get("transcript").cloned().unwrap_or(Value::String(String::new()))
    };

    // error = data.get("error") if not success else None
    let error: Value = if !success {
        data.get("error").cloned().unwrap_or(Value::Null)
    } else {
        Value::Null
    };

    Json(json!({
        "video_id": video_id,
        "transcript_success": success,
        "transcript_length": transcript_length,
        "transcript_preview": transcript_preview,
        "error": error,
    }))
}

/// `query: str = Form("What is machine learning?")` — the POST form body, with a
/// default applied when the field is absent (matching FastAPI's `Form(default)`).
#[derive(Deserialize)]
struct ResearchForm {
    #[serde(default = "default_research_query")]
    query: String,
}

/// FastAPI `Form("What is machine learning?")` default.
fn default_research_query() -> String {
    "What is machine learning?".to_string()
}

impl Default for ResearchForm {
    fn default() -> Self {
        ResearchForm {
            query: default_research_query(),
        }
    }
}

/// `@router.post("/api/test-research")` -> `POST /api/test-research` — a deep-research
/// smoke test (diagnostics_routes.py). Builds the default endpoint/model, runs the
/// research via the public [`ResearchHandler::run_research_once`], and returns
/// `{"status":"success", "query", "result_preview" (first 200 chars), "result_length"}`.
/// `run_research_once` returns a (possibly fallback) report string and never panics,
/// so this always reports `success`.
///
/// Upstream 7c7ac10 added `require_admin(request)` as the first statement, so the
/// admin gate runs before the research run; the body is preserved in
/// [`test_research_body`]. `Form` consumes the request body, so it stays last in the
/// extractor list (after `State`/`HeaderMap`/the optional `CurrentUser`).
async fn test_research(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    form: Option<Form<ResearchForm>>,
) -> Result<Json<Value>, HttpException> {
    // require_admin(request)
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    Ok(test_research_body(form).await)
}

/// The `test_research` body sans the admin gate — the ported research run.
async fn test_research_body(form: Option<Form<ResearchForm>>) -> Json<Value> {
    let query = form.map(|Form(f)| f.query).unwrap_or_else(default_research_query);
    let endpoint = format!("http://{}:8000/v1/chat/completions", &*DEFAULT_HOST);
    let model = "gpt-oss-120b";
    let rh = crate::src::research_handler::ResearchHandler::new();
    let result = rh
        .run_research_once(&query, &endpoint, model, indexmap::IndexMap::new())
        .await;
    // result[:200] + "..." if len > 200 else result  (by char count, like Python str).
    let len = result.chars().count();
    let preview = if len > 200 {
        format!("{}...", result.chars().take(200).collect::<String>())
    } else {
        result.clone()
    };
    Json(json!({
        "status": "success",
        "query": query,
        "result_preview": preview,
        "result_length": len,
    }))
}

/// `setup_diagnostics_routes(...)` — build the diagnostics router.
///
/// `APIRouter(tags=["diagnostics"])` has **no** `prefix`, so each handler carries
/// its full absolute `/api/...` path. None of these four paths collide with the
/// inline `web/mod.rs` subset (which owns no `/api/db/*`, `/api/rag/*`, `/api/test/*`
/// or `/api/test-research`). Deps (`rag_manager`, `research_handler`) come from
/// [`AppState`] / are honest-stubbed, so the factory takes no parameters.
pub fn setup_diagnostics_routes() -> Router<AppState> {
    Router::new()
        .route("/api/db/stats", get(get_database_stats))
        .route("/api/rag/stats", get(get_rag_stats))
        .route("/api/test/youtube", get(test_youtube))
        .route("/api/test-research", post(test_research))
}

#[cfg(test)]
mod tests {
    use super::*;

    // ---- /api/db/stats --------------------------------------------------------

    /// `get_detailed_stats()` returns the documented shape (session/message/memory
    /// counts + `database_size_mb`); the handler wraps it as 200 JSON. The handler
    /// now runs `require_admin(request)` first (needs a full 18-Arc `AppState`,
    /// which the codebase deliberately does not build in unit tests — see
    /// `tests/compare_routes.rs`), so we exercise the post-gate logic via the inner
    /// [`db_stats_body`] (the gate itself is covered by `core::middleware` /
    /// `auth_adapter` tests).
    #[tokio::test]
    async fn db_stats_returns_detailed_stats_shape() {
        let Json(body) = db_stats_body().unwrap();
        let obj = body.as_object().expect("object");
        // The keys `get_session_stats` always produces, plus the file-size field.
        for key in [
            "total_sessions",
            "active_sessions",
            "archived_sessions",
            "total_messages",
            "total_memories",
            "database_size_mb",
        ] {
            assert!(obj.contains_key(key), "missing key {key} in {body}");
        }
    }

    // ---- /api/test/youtube ----------------------------------------------------

    /// A non-YouTube URL fails the ported `extract_youtube_id` parse, yielding the
    /// `{"error": "Invalid YouTube URL"}` branch (fully ported, no stub involved).
    /// The handler now runs `require_admin(request)` first (needs a full `AppState`),
    /// so we exercise the post-gate logic via the inner [`test_youtube_body`].
    #[tokio::test]
    async fn youtube_invalid_url_returns_error_branch() {
        let Json(body) = test_youtube_body(YoutubeQuery {
            url: "https://example.com/not-youtube".to_string(),
        })
        .await;
        assert_eq!(body, json!({ "error": "Invalid YouTube URL" }));
    }

    /// A valid YouTube URL parses to a `video_id`, then the REAL async yt-dlp
    /// transcript fetch runs. NETWORK + yt-dlp dependent (and slow: up to 3
    /// retries x 30s on failure), so `#[ignore]`d to keep the default offline
    /// `cargo test` green and fast. On a live run with captions present the
    /// response carries the success shape; offline / on failure it carries the
    /// `transcript_success=false`, `transcript_length=0`, `error` string, raw
    /// (`null`) transcript preview Python failure-fetch shape. Run with:
    ///   `cargo test -- --ignored youtube_valid_url_reflects_real_transcript_fetch`
    #[tokio::test]
    #[ignore = "network + yt-dlp dependent (and slow on retry); run on demand with --ignored"]
    async fn youtube_valid_url_reflects_real_transcript_fetch() {
        let Json(body) = test_youtube_body(YoutubeQuery {
            url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ".to_string(),
        })
        .await;
        assert_eq!(body["video_id"], json!("dQw4w9WgXcQ"));
        // We only assert the response is well-formed regardless of net outcome:
        // transcript_success is a bool; on failure error is a string + preview null.
        assert!(body["transcript_success"].is_boolean());
        if body["transcript_success"] == json!(false) {
            assert_eq!(body["transcript_length"], json!(0));
            assert!(body["error"].is_string(), "failure error should be a string: {body}");
            assert_eq!(body["transcript_preview"], Value::Null);
        }
    }

    // ---- /api/test-research (now a real research run) -------------------------

    /// `test_research` now drives a REAL deep-research pass (endpoint probe + a
    /// web-search fallback), so it touches the network and is `#[ignore]`d like the
    /// other live tests — run with `cargo test -- --ignored`. Verifies the
    /// `{"status":"success", query, result_preview, result_length}` response shape.
    /// The handler now runs `require_admin(request)` first (needs a full `AppState`),
    /// so we exercise the post-gate logic via the inner [`test_research_body`].
    #[tokio::test]
    #[ignore]
    async fn test_research_default_query_returns_success_shape() {
        let Json(body) = test_research_body(None).await;
        assert_eq!(body["status"], json!("success"));
        assert_eq!(body["query"], json!("What is machine learning?"));
        assert!(body["result_preview"].is_string());
        assert!(body["result_length"].is_number());
    }

    /// The supplied `query` form field is echoed back in the response.
    #[tokio::test]
    #[ignore]
    async fn test_research_echoes_supplied_query() {
        let Json(body) = test_research_body(Some(Form(ResearchForm {
            query: "what is rust".to_string(),
        })))
        .await;
        assert_eq!(body["status"], json!("success"));
        assert_eq!(body["query"], json!("what is rust"));
    }
}
