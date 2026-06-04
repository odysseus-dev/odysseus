// routes/tts_routes.rs  <- routes/tts_routes.py
//! TTS API routes — multi-provider (local Kokoro, API endpoint, browser).
//!
//! Proof-batch module **P11-tts** (wave: proof). Three no-auth handlers on the
//! collision-free `/api/tts/*` prefix (so it coexists with the inline
//! `web/mod.rs` subset — the additive / non-breaking rule):
//!
//!   * `GET  /api/tts/stats`       — `tts_service.get_stats()`
//!   * `POST /api/tts/synthesize`  — synthesize text -> audio bytes / base64
//!   * `POST /api/tts/clear-cache` — `tts_service.clear_cache()`
//!
//! Python factory:
//!
//! ```python
//! class TTSRequest(BaseModel):
//!     text: str
//!     format: str = "audio"  # "audio" or "base64"
//!
//! def setup_tts_routes(tts_service):
//!     router = APIRouter(prefix="/api/tts", tags=["tts"])
//!     @router.get("/stats") ...
//!     @router.post("/synthesize") ...
//!     @router.post("/clear-cache") ...
//!     return router
//! ```
//!
//! ## Service handle
//! The Python factory receives `tts_service` as a parameter; the design pins TTS
//! as a **`&'static` singleton** (`services::tts::get_tts_service()`), NOT an
//! `AppState` field, so [`setup_tts_routes`] takes no parameters and each handler
//! reaches the singleton directly. `State<AppState>` is therefore not extracted.
//!
//! ## Dict-detail `HTTPException`
//! `/synthesize` raises `HTTPException(status, detail={"message": ...})` — a
//! *dict* detail, not a string. This is the module that EXERCISES the foundation
//! F1 dict-detail path: those raises map to
//! [`HttpException::with_detail`], which renders `{"detail": {"message": ...}}`
//! exactly as Starlette's default handler does. The two string-detail handlers
//! (`/stats`, `/clear-cache`) use the ordinary [`HttpException::new`].


use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::Deserialize;
use serde_json::json;

use crate::routes::{AppState, HttpException};
use crate::services::tts::get_tts_service;

/// `class TTSRequest(BaseModel): text: str; format: str = "audio"`.
///
/// `text` is required (Pydantic raises 422 if absent); `format` defaults to
/// `"audio"` (the other accepted value is `"base64"`). `#[serde(default)]` on
/// `format` reproduces the Pydantic default so a body of just `{"text": "..."}`
/// deserialises with `format == "audio"`.
#[derive(Debug, Deserialize)]
struct TtsRequest {
    text: String,
    #[serde(default = "default_format")]
    format: String,
}

/// `format: str = "audio"` — the Pydantic field default.
fn default_format() -> String {
    "audio".to_string()
}

/// `@router.get("/stats")` — `async def get_tts_stats():`
///
/// ```python
/// try:
///     return tts_service.get_stats()
/// except Exception as e:
///     logger.error(f"Failed to get TTS stats: {e}")
///     raise HTTPException(status_code=500, detail=str(e))
/// ```
///
/// The Rust `get_stats()` is infallible (it never returns a `Result`), so the
/// `except` arm is unreachable — exactly as in Python on the host path, where
/// `get_stats` only does settings + filesystem reads. We return the stats map
/// directly as JSON, preserving the `serde_json::Map` insertion order that
/// matches the Python dict key order.
async fn get_tts_stats() -> Result<Response, HttpException> {
    let stats = get_tts_service().get_stats();
    Ok(Json(serde_json::Value::Object(stats)).into_response())
}

/// `@router.post("/synthesize")` — `async def synthesize_speech(request: TTSRequest):`
///
/// ```python
/// try:
///     if not tts_service.available:
///         raise HTTPException(503, detail={"message": "TTS service not available"})
///     if request.format == "base64":
///         audio_b64 = tts_service.synthesize_to_base64(request.text)
///         if not audio_b64:
///             raise HTTPException(500, detail={"message": "Synthesis failed"})
///         return {"audio": audio_b64}
///     else:  # audio format
///         audio_data = tts_service.synthesize(request.text)
///         if not audio_data:
///             raise HTTPException(500, detail={"message": "Synthesis failed"})
///         is_mp3 = audio_data[:3] == b'ID3' or (len(audio_data) >= 2 and audio_data[0] == 0xff and (audio_data[1] & 0xe0) == 0xe0)
///         mime = "audio/mpeg" if is_mp3 else "audio/wav"
///         return Response(content=audio_data, media_type=mime, headers={"Content-Disposition": ...})
/// except HTTPException:
///     raise
/// except Exception as e:
///     logger.error(f"Synthesis error: {e}", exc_info=True)
///     raise HTTPException(500, detail={"message": f"Synthesis failed: {str(e)}"})
/// ```
///
/// Every error path raises a *dict* detail (`detail={"message": ...}`), mapped to
/// [`HttpException::with_detail`]. The bare-`except` arm cannot be reached from
/// the Rust calls (`synthesize`/`synthesize_to_base64` return `Option`, never
/// panic on the host path), but the structure is preserved 1:1.
async fn synthesize_speech(Json(request): Json<TtsRequest>) -> Result<Response, HttpException> {
    let tts = get_tts_service();

    // if not tts_service.available: raise HTTPException(503, {"message": ...})
    if !tts.available() {
        return Err(HttpException::with_detail(
            503,
            json!({ "message": "TTS service not available" }),
        ));
    }

    if request.format == "base64" {
        // audio_b64 = tts_service.synthesize_to_base64(request.text)
        // Offloaded to a blocking thread: the local Kokoro path drives the async
        // kokoro-tts API via block_on, and the endpoint path uses reqwest::blocking
        // — neither may run on an async worker. A panic (JoinError) -> None -> 500.
        let text = request.text.clone();
        let audio_b64 = tokio::task::spawn_blocking(move || tts.synthesize_to_base64(&text))
            .await
            .ok()
            .flatten();
        // if not audio_b64: raise HTTPException(500, {"message": "Synthesis failed"})
        // Python's `not audio_b64` is also true for an empty string, so an empty
        // result fails the same way; mirror with `is_empty`.
        match audio_b64 {
            Some(b64) if !b64.is_empty() => Ok(Json(json!({ "audio": b64 })).into_response()),
            _ => Err(HttpException::with_detail(
                500,
                json!({ "message": "Synthesis failed" }),
            )),
        }
    } else {
        // audio format
        // audio_data = tts_service.synthesize(request.text)  (use_cache defaults True)
        // Offloaded to a blocking thread (see the base64 branch).
        let text = request.text.clone();
        let audio_data = tokio::task::spawn_blocking(move || tts.synthesize(&text, true))
            .await
            .ok()
            .flatten();
        // if not audio_data: raise HTTPException(500, {"message": "Synthesis failed"})
        // `not audio_data` is true for `None` AND for empty bytes -> mirror both.
        let audio_data = match audio_data {
            Some(d) if !d.is_empty() => d,
            _ => {
                return Err(HttpException::with_detail(
                    500,
                    json!({ "message": "Synthesis failed" }),
                ))
            }
        };

        // Detect format from magic bytes (MP3: ID3 tag or sync word ff e0+).
        //   is_mp3 = audio_data[:3] == b'ID3'
        //            or (len >= 2 and audio_data[0] == 0xff and (audio_data[1] & 0xe0) == 0xe0)
        let is_mp3 = (audio_data.len() >= 3 && &audio_data[..3] == b"ID3")
            || (audio_data.len() >= 2 && audio_data[0] == 0xff && (audio_data[1] & 0xe0) == 0xe0);
        // mime = "audio/mpeg" if is_mp3 else "audio/wav"
        let mime = if is_mp3 { "audio/mpeg" } else { "audio/wav" };
        // Content-Disposition: "inline; filename=speech.mp3" if "mpeg" in mime else "...wav"
        let disposition = if mime.contains("mpeg") {
            "inline; filename=speech.mp3"
        } else {
            "inline; filename=speech.wav"
        };
        Ok(audio_response(audio_data, mime, disposition))
    }
}

/// `@router.post("/clear-cache")` — `async def clear_tts_cache():`
///
/// ```python
/// try:
///     tts_service.clear_cache()
///     return {"success": True, "message": "Cache cleared"}
/// except Exception as e:
///     logger.error(f"Failed to clear cache: {e}")
///     raise HTTPException(status_code=500, detail=str(e))
/// ```
///
/// `clear_cache()` is infallible in the Rust port (best-effort FS removals that
/// swallow errors), so the `except`/`logger.error` arm is unreachable, mirroring
/// the Python host path.
async fn clear_tts_cache() -> Result<Response, HttpException> {
    get_tts_service().clear_cache();
    Ok(Json(json!({ "success": true, "message": "Cache cleared" })).into_response())
}

/// `return Response(content=audio_data, media_type=mime, headers={"Content-Disposition": ...})`.
///
/// Builds the raw-audio response with the sniffed `Content-Type` and the
/// `inline` `Content-Disposition`. Infallible: a `Response` from owned bytes +
/// two static-shaped headers never fails to construct (the `mime`/`disposition`
/// are always valid header values), so `unwrap()` matches the Python which
/// cannot raise here either.
fn audio_response(audio_data: Vec<u8>, mime: &str, disposition: &str) -> Response {
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, mime)
        .header(header::CONTENT_DISPOSITION, disposition)
        .body(axum::body::Body::from(audio_data))
        .unwrap()
}

/// `setup_tts_routes(tts_service)` — `APIRouter(prefix="/api/tts", tags=["tts"])`.
///
/// Mounts the three handlers at the absolute paths the FastAPI
/// `prefix="/api/tts"` yields. The `tts_service` factory argument is dropped: the
/// service is the process-wide `&'static` singleton reached directly in each
/// handler (the design's TTS decision), so the factory takes no parameters and
/// the router carries no per-handler state.
pub fn setup_tts_routes() -> Router<AppState> {
    Router::new()
        .route("/api/tts/stats", get(get_tts_stats))
        .route("/api/tts/synthesize", post(synthesize_speech))
        .route("/api/tts/clear-cache", post(clear_tts_cache))
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::to_bytes;

    async fn body_bytes(resp: Response) -> Vec<u8> {
        to_bytes(resp.into_body(), usize::MAX)
            .await
            .unwrap()
            .to_vec()
    }

    async fn body_json(resp: Response) -> serde_json::Value {
        serde_json::from_slice(&body_bytes(resp).await).unwrap()
    }

    #[test]
    fn ttsrequest_format_defaults_to_audio() {
        // Body of just `{"text": "..."}` -> format == "audio" (Pydantic default).
        let r: TtsRequest = serde_json::from_str(r#"{"text":"hi"}"#).unwrap();
        assert_eq!(r.text, "hi");
        assert_eq!(r.format, "audio");
    }

    #[test]
    fn ttsrequest_explicit_format_kept() {
        let r: TtsRequest = serde_json::from_str(r#"{"text":"hi","format":"base64"}"#).unwrap();
        assert_eq!(r.format, "base64");
    }

    #[test]
    fn ttsrequest_missing_text_is_error() {
        // `text` is required (Pydantic 422); serde rejects the absent field.
        assert!(serde_json::from_str::<TtsRequest>(r#"{"format":"audio"}"#).is_err());
    }

    #[tokio::test]
    async fn synthesize_unavailable_renders_dict_detail() {
        // The host path has TTS provider "disabled" by default -> not available
        // -> the 503 dict-detail branch, exercising the F1 with_detail path.
        let resp = synthesize_speech(Json(TtsRequest {
            text: "hello".into(),
            format: "audio".into(),
        }))
        .await
        .unwrap_err()
        .into_response();
        assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
        assert_eq!(
            body_json(resp).await,
            json!({ "detail": { "message": "TTS service not available" } })
        );
    }

    #[tokio::test]
    async fn synthesize_base64_unavailable_also_dict_detail() {
        // base64 branch hits the same `available` gate first (503 dict detail).
        let resp = synthesize_speech(Json(TtsRequest {
            text: "hello".into(),
            format: "base64".into(),
        }))
        .await
        .unwrap_err()
        .into_response();
        assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
        assert_eq!(
            body_json(resp).await,
            json!({ "detail": { "message": "TTS service not available" } })
        );
    }

    #[tokio::test]
    async fn stats_returns_object_with_expected_keys() {
        let resp = get_tts_stats().await.unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let v = body_json(resp).await;
        assert!(v.is_object(), "stats must be a JSON object");
        // Faithful to get_stats(): always carries these keys.
        for k in [
            "available",
            "ready",
            "provider",
            "model",
            "voice",
            "speed",
            "cache_entries",
            "cache_size_mb",
        ] {
            assert!(v.get(k).is_some(), "stats missing key `{k}`");
        }
    }

    #[tokio::test]
    async fn clear_cache_returns_success_shape() {
        let resp = clear_tts_cache().await.unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        assert_eq!(
            body_json(resp).await,
            json!({ "success": true, "message": "Cache cleared" })
        );
    }

    #[test]
    fn audio_response_mp3_headers() {
        // ID3-prefixed bytes -> audio/mpeg + speech.mp3 disposition.
        let resp = audio_response(b"ID3xxxx".to_vec(), "audio/mpeg", "inline; filename=speech.mp3");
        assert_eq!(resp.status(), StatusCode::OK);
        assert_eq!(resp.headers().get(header::CONTENT_TYPE).unwrap(), "audio/mpeg");
        assert_eq!(
            resp.headers().get(header::CONTENT_DISPOSITION).unwrap(),
            "inline; filename=speech.mp3"
        );
    }

    #[test]
    fn audio_response_wav_headers() {
        let resp = audio_response(b"RIFFxxxx".to_vec(), "audio/wav", "inline; filename=speech.wav");
        assert_eq!(resp.headers().get(header::CONTENT_TYPE).unwrap(), "audio/wav");
        assert_eq!(
            resp.headers().get(header::CONTENT_DISPOSITION).unwrap(),
            "inline; filename=speech.wav"
        );
    }
}
