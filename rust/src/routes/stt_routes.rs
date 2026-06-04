// routes/stt_routes.rs  <- routes/stt_routes.py
//! STT API routes — multi-provider (local Whisper, API endpoint, browser).
//!
//! Proof-batch unit **P12-stt** — the smallest router. Two no-auth handlers:
//! `GET /api/stt/stats` and `POST /api/stt/transcribe`. Validates the
//! `UploadFile -> Multipart` single-`file`-field-to-bytes pattern and the
//! `&'static` STT service singleton ([`crate::services::stt::get_stt_service`]).
//!
//! ## FastAPI -> axum mapping
//!
//! The Python factory `setup_stt_routes(stt_service)` builds an
//! `APIRouter(prefix="/api/stt", tags=["stt"])` with two nested closures
//! capturing `stt_service`. The axum analogue [`setup_stt_routes`] returns a
//! `Router<AppState>`; deps are not factory args — the service is the
//! process-wide `&'static` singleton fetched inside each handler via
//! `get_stt_service()` (the Python `stt_service` global resolves to the same
//! shared instance every call), so the factory takes no parameters. Each route
//! is mounted at the full absolute path `"/api/stt" + subpath`.
//!
//! ## Dict-detail HTTPException
//!
//! `transcribe` raises `HTTPException(status, detail={"message": ...})` — a
//! structured (dict) detail, not a bare string. Those map to
//! [`HttpException::with_detail`] so the rendered body is
//! `{"detail": {"message": ...}}`, exactly Starlette's default handler (see the
//! foundation `impl IntoResponse for HttpException` in [`crate::routes`]). The
//! `/stats` handler's `detail=str(e)` is a plain-string detail and uses
//! [`HttpException::new`]; in practice `get_stats()` is infallible in the Rust
//! port (it never raises), mirroring the Python `try/except` that effectively
//! never fires.

use crate::pylog as logger;
use crate::routes::{AppState, HttpException};
use crate::services::stt::get_stt_service;
use crate::src::upload_limits::read_upload_limited;
use axum::extract::Multipart;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde_json::{json, Value};

/// `STT_MAX_AUDIO_BYTES = 25 * 1024 * 1024` — mirrors the Python constant.
///
/// ```python
/// STT_MAX_AUDIO_BYTES = 25 * 1024 * 1024
/// ```
const STT_MAX_AUDIO_BYTES: usize = 25 * 1024 * 1024;

/// `setup_stt_routes(stt_service)` — assemble the STT router.
///
/// ```python
/// def setup_stt_routes(stt_service):
///     router = APIRouter(prefix="/api/stt", tags=["stt"])
///     ...
///     return router
/// ```
///
/// `prefix="/api/stt"` -> each handler mounted at `/api/stt` + its subpath. No
/// auth, no DB; the `stt_service` factory arg is the `&'static` singleton, so
/// the Rust factory takes no parameters.
pub fn setup_stt_routes() -> Router<AppState> {
    Router::new()
        .route("/api/stt/stats", get(get_stt_stats))
        .route("/api/stt/transcribe", post(transcribe_audio))
}

/// `GET /api/stt/stats` — get STT service statistics.
///
/// ```python
/// @router.get("/stats")
/// async def get_stt_stats():
///     try:
///         return stt_service.get_stats()
///     except Exception as e:
///         logger.error(f"Failed to get STT stats: {e}")
///         raise HTTPException(status_code=500, detail=str(e))
/// ```
///
/// `get_stats()` is infallible in the Rust port (it returns a `Map`, never an
/// error), so the `except`/500 branch is unreachable — the same observable
/// result as the Python whose `try` body cannot raise in normal operation.
async fn get_stt_stats() -> Result<Response, HttpException> {
    let stats = get_stt_service().get_stats();
    Ok(Json(Value::Object(stats)).into_response())
}

/// `POST /api/stt/transcribe` — transcribe an uploaded audio file to text.
///
/// ```python
/// @router.post("/transcribe")
/// async def transcribe_audio(file: UploadFile = File(...)):
///     try:
///         if not stt_service.available:
///             raise HTTPException(503, detail={"message": "STT service not available or set to browser mode"})
///         audio_bytes = await file.read()
///         if not audio_bytes:
///             raise HTTPException(400, detail={"message": "Empty audio file"})
///         text = stt_service.transcribe(audio_bytes)
///         if text is None:
///             raise HTTPException(500, detail={"message": "Transcription failed"})
///         return {"text": text}
///     except HTTPException:
///         raise
///     except Exception as e:
///         logger.error(f"Transcription error: {e}", exc_info=True)
///         raise HTTPException(500, detail={"message": f"Transcription failed: {str(e)}"})
/// ```
///
/// `UploadFile = File(...)` -> a required `multipart/form-data` field named
/// `file`; its bytes are `await file.read()`. The availability check happens
/// BEFORE reading the file, exactly as in Python (the order is observable: a
/// disabled service returns 503 even for an empty/malformed upload).
async fn transcribe_audio(mp: Multipart) -> Result<Response, HttpException> {
    let stt = get_stt_service();

    // `if not stt_service.available:` — checked before reading the upload.
    if !stt.available() {
        return Err(HttpException::with_detail(
            503,
            json!({"message": "STT service not available or set to browser mode"}),
        ));
    }

    // `audio_bytes = await read_upload_limited(file, STT_MAX_AUDIO_BYTES, "Audio file")`
    // (commit 193dc2f — previously `await file.read()`, now bounded).
    // A missing `file` field is FastAPI's 422 request-validation error for the
    // unsatisfied `File(...)` dependency; reproduce that status. An over-limit
    // upload returns a 413 `HttpException` from `read_upload_limited`; Python
    // re-raises it via `except HTTPException: raise`, so we propagate it directly
    // with `?`. A low-level multipart stream error maps to the broad-`except` 500.
    let audio_bytes = match read_file_field(mp, STT_MAX_AUDIO_BYTES, "Audio file").await {
        Ok(Some(bytes)) => bytes,
        Ok(None) => {
            // `File(...)` is required: no `file` part -> 422 (FastAPI validation).
            return Err(HttpException::new(422, "field required: file"));
        }
        // Propagate as-is: a 413 from the size cap, or the 500 multipart error
        // constructed inside `read_file_field`. Python's `except HTTPException:
        // raise` means we must not re-wrap it.
        Err(e) => return Err(e),
    };

    // `if not audio_bytes:` — an empty upload is a 400.
    if audio_bytes.is_empty() {
        return Err(HttpException::with_detail(
            400,
            json!({"message": "Empty audio file"}),
        ));
    }

    // `text = stt_service.transcribe(audio_bytes)`. `transcribe` does blocking
    // IO (reqwest::blocking for the endpoint path), so run it off the async
    // runtime. A panic inside (unexpected) surfaces as the broad-`except` 500.
    let text = match tokio::task::spawn_blocking(move || stt.transcribe(&audio_bytes)).await {
        Ok(t) => t,
        Err(e) => {
            logger::error(&format!("Transcription error: {e}"));
            return Err(HttpException::with_detail(
                500,
                json!({"message": format!("Transcription failed: {e}")}),
            ));
        }
    };

    // `if text is None: raise HTTPException(500, {"message": "Transcription failed"})`.
    match text {
        Some(text) => Ok(Json(json!({ "text": text })).into_response()),
        None => Err(HttpException::with_detail(
            500,
            json!({"message": "Transcription failed"}),
        )),
    }
}

/// Read the single `UploadFile = File(...)` field named `file` from a
/// `multipart/form-data` body, returning its bytes.
///
/// FastAPI's `file: UploadFile = File(...)` resolves a multipart part named
/// `file`; `await file.read()` yields its bytes. We scan parts for the first
/// named `file` and return its bytes; `Ok(None)` means the body had no such
/// part (the required-field 422 case); `Err` means either the multipart stream
/// itself failed (broad-`except` 500 case) or the upload exceeded
/// [`STT_MAX_AUDIO_BYTES`] (413 — Python's `except HTTPException: raise`).
/// Other fields are ignored, matching FastAPI binding only the declared `file`
/// parameter.
///
/// ## Size cap (commit 193dc2f)
///
/// The Python changed `await file.read()` to
/// `await read_upload_limited(file, STT_MAX_AUDIO_BYTES, "Audio file")`.
/// We delegate to [`read_upload_limited`] with the same cap and label so that
/// oversized bodies are rejected with a 413 `HttpException` before any bytes
/// are forwarded to the transcription engine.
async fn read_file_field(
    mut mp: Multipart,
    limit: usize,
    label: &str,
) -> Result<Option<Vec<u8>>, HttpException> {
    while let Some(field) = mp
        .next_field()
        .await
        .map_err(|e| HttpException::new(500, format!("Transcription failed: {e}")))?
    {
        if field.name() == Some("file") {
            // `audio_bytes = await read_upload_limited(file, STT_MAX_AUDIO_BYTES, "Audio file")`
            let bytes = read_upload_limited(field, limit, label).await?;
            return Ok(Some(bytes));
        }
    }
    Ok(None)
}

#[cfg(test)]
mod tests {
    //! Parity tests for P12-stt. The two handlers take no `State<AppState>` (they
    //! use the `&'static` singleton + `Multipart` only), so they mount on a
    //! `Router<()>` directly — this exercises the full extractor + `IntoResponse`
    //! path (Multipart parse, the dict-detail body) without standing up the heavy
    //! `AppState`, matching the unit-test style of the other proof-batch modules.
    //!
    //! With no `data/settings.json` the STT service resolves to `disabled`, so
    //! `available()` is deterministically `false` here; the tests therefore cover
    //! `/stats` (always-200 Map) and the 503 unavailable branch + its ordering.

    use super::*;
    use axum::body::Body;
    use axum::http::{Request, StatusCode};
    use tower::ServiceExt; // for `oneshot`

    /// A `Router<()>` mounting the same handlers as [`setup_stt_routes`]. Both
    /// handlers are state-free, so this is type-equivalent to the real router for
    /// the paths the tests drive.
    fn app() -> Router {
        Router::new()
            .route("/api/stt/stats", get(get_stt_stats))
            .route("/api/stt/transcribe", post(transcribe_audio))
    }

    async fn body_json(resp: Response) -> Value {
        let bytes = axum::body::to_bytes(resp.into_body(), usize::MAX)
            .await
            .unwrap();
        serde_json::from_slice(&bytes).unwrap()
    }

    /// A `multipart/form-data` body with one part. `name` selects the field
    /// name; `data` is the raw part bytes.
    fn multipart_body(name: &str, data: &[u8]) -> (String, Vec<u8>) {
        let boundary = "TESTBOUNDARY1234";
        let mut body = Vec::new();
        body.extend_from_slice(format!("--{boundary}\r\n").as_bytes());
        body.extend_from_slice(
            format!(
                "Content-Disposition: form-data; name=\"{name}\"; filename=\"audio.webm\"\r\n"
            )
            .as_bytes(),
        );
        body.extend_from_slice(b"Content-Type: audio/webm\r\n\r\n");
        body.extend_from_slice(data);
        body.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());
        (
            format!("multipart/form-data; boundary={boundary}"),
            body,
        )
    }

    #[tokio::test]
    async fn stats_returns_object_with_expected_keys() {
        // `get_stt_stats` always returns the `get_stats()` Map; with no settings
        // the service is `disabled` => `available: false`, `provider: "disabled"`.
        let resp = app()
            .oneshot(
                Request::builder()
                    .uri("/api/stt/stats")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let v = body_json(resp).await;
        assert!(v.get("available").is_some());
        assert!(v.get("provider").is_some());
        assert!(v.get("model").is_some());
        assert!(v.get("language").is_some());
    }

    #[tokio::test]
    async fn transcribe_unavailable_returns_503_dict_detail() {
        // Default settings => STT disabled => `not available` => 503 with the
        // dict detail, checked BEFORE the upload is read.
        let (ct, body) = multipart_body("file", b"\x00\x01audio");
        let resp = app()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/api/stt/transcribe")
                    .header("content-type", ct)
                    .body(Body::from(body))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
        assert_eq!(
            body_json(resp).await,
            json!({"detail": {"message": "STT service not available or set to browser mode"}})
        );
    }

    #[tokio::test]
    async fn transcribe_unavailable_even_with_no_file_field() {
        // The availability check precedes file reading, so a missing `file`
        // field still yields 503 (not 422) when the service is disabled —
        // exactly the Python ordering.
        let (ct, body) = multipart_body("other", b"junk");
        let resp = app()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/api/stt/transcribe")
                    .header("content-type", ct)
                    .body(Body::from(body))
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
    }

    /// `read_file_field` delegates to `read_upload_limited` with `STT_MAX_AUDIO_BYTES`.
    ///
    /// We can only test the 413 path indirectly via the handler because in this
    /// test environment `get_stt_service().available()` is false — the 503 fires
    /// before the file is read. So we test `read_file_field` in isolation.
    ///
    /// Mirrors commit 193dc2f: previously `await file.read()` was unbounded;
    /// now an over-limit body returns 413 before any transcription attempt.
    #[tokio::test]
    async fn read_file_field_over_limit_returns_413() {
        // Use a 1 MB cap (formatted "1 MB") so the test body stays under axum's
        // 2 MB default Multipart limit — the bounding logic is identical to the
        // real STT_MAX_AUDIO_BYTES cap, which the live route disables the body
        // limit for (build_router DefaultBodyLimit::disable). One byte over -> 413.
        const CAP: usize = 1024 * 1024;
        let oversized = vec![0u8; CAP + 1];
        let (ct, body) = multipart_body("file", &oversized);

        // Parse the multipart body directly and call `read_file_field`.
        use axum::body::Body;
        use axum::extract::FromRequest;
        let req = axum::http::Request::builder()
            .header("content-type", ct)
            .body(Body::from(body))
            .unwrap();
        let mp = Multipart::from_request(req, &()).await.unwrap();
        let err = read_file_field(mp, CAP, "Audio file").await.unwrap_err();
        assert_eq!(err.status_code, 413);
        assert!(
            err.detail.contains("Audio file"),
            "detail should contain label 'Audio file': {}",
            err.detail
        );
        assert!(
            err.detail.contains("1 MB"),
            "detail should contain formatted cap '1 MB': {}",
            err.detail
        );
    }

    /// A body at exactly the cap is accepted (not over the cap).
    #[tokio::test]
    async fn read_file_field_at_limit_returns_ok() {
        const CAP: usize = 1024 * 1024;
        let at_limit = vec![0xABu8; CAP];
        let (ct, body) = multipart_body("file", &at_limit);

        use axum::body::Body;
        use axum::extract::FromRequest;
        let req = axum::http::Request::builder()
            .header("content-type", ct)
            .body(Body::from(body))
            .unwrap();
        let mp = Multipart::from_request(req, &()).await.unwrap();
        let result = read_file_field(mp, CAP, "Audio file").await.unwrap();
        // `Ok(Some(bytes))` with the correct length.
        assert_eq!(result.unwrap().len(), CAP);
    }
}
