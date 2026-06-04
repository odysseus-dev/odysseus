// src/upload_limits.rs  <- src/upload_limits.py
//! Small helpers for route-local upload size caps.
//!
//! Direct port of commit 193dc2f ("fix(uploads): bound direct upload reads").
//!
//! ## Python origin
//! ```python
//! def format_byte_limit(limit: int) -> str: ...
//! async def read_upload_limited(upload: UploadFile, limit: int, label: str = "Upload") -> bytes: ...
//! ```
//!
//! ## Rust adaptation
//! FastAPI's `UploadFile` → axum's [`axum::extract::multipart::Field`] (a field
//! extracted from a `Multipart` extractor). The Python `await upload.read(limit+1)`
//! reads at most `limit+1` bytes from the async stream; the Rust port drains the
//! field's chunk stream, accumulating into a `Vec<u8>` and stopping as soon as the
//! buffer exceeds `limit` bytes (at that point we have read enough to know the
//! upload is over-limit — exactly the same sentinel-read strategy).
//!
//! [`format_byte_limit`] is a pure function exposed `pub` so route files can use it
//! in their own error messages if needed (mirrors the Python `from src.upload_limits
//! import format_byte_limit`).

use axum::extract::multipart::Field;

use crate::routes::HttpException;

// ── format_byte_limit ──────────────────────────────────────────────────────────

/// Format a byte count as a human-readable string.
///
/// ```python
/// def format_byte_limit(limit: int) -> str:
///     if limit % (1024 * 1024) == 0:
///         return f"{limit // (1024 * 1024)} MB"
///     if limit % 1024 == 0:
///         return f"{limit // 1024} KB"
///     return f"{limit} bytes"
/// ```
pub fn format_byte_limit(limit: usize) -> String {
    if limit.is_multiple_of(1024 * 1024) {
        format!("{} MB", limit / (1024 * 1024))
    } else if limit.is_multiple_of(1024) {
        format!("{} KB", limit / 1024)
    } else {
        format!("{} bytes", limit)
    }
}

// ── read_upload_limited ────────────────────────────────────────────────────────

/// Read a multipart `Field` with a hard byte cap.
///
/// ```python
/// async def read_upload_limited(upload: UploadFile, limit: int, label: str = "Upload") -> bytes:
///     data = await upload.read(limit + 1)
///     if len(data) > limit:
///         raise HTTPException(
///             status_code=413,
///             detail=f"{label} exceeds {format_byte_limit(limit)} limit",
///         )
///     return data
/// ```
///
/// ## Strategy
/// Python's `await upload.read(limit + 1)` reads at most `limit+1` bytes; if the
/// upload contains more, the call returns exactly `limit+1` bytes and `len > limit`
/// fires the 413. We replicate this sentinel approach by accumulating chunk bytes
/// until the buffer length exceeds `limit`, at which point we return 413 immediately
/// without reading the rest of the stream — same observable semantics as the Python.
///
/// ## Error mapping
/// * A multipart stream error (malformed body) → `HttpException::new(400, ...)`.
/// * Over-limit → `HttpException::new(413, ...)`.
/// * Success → `Ok(Vec<u8>)`.
pub async fn read_upload_limited(
    mut field: Field<'_>,
    limit: usize,
    label: &str,
) -> Result<Vec<u8>, HttpException> {
    let mut buf: Vec<u8> = Vec::new();

    // Drain chunks from the multipart field, capping at limit+1 bytes.
    // `Field::chunk()` takes `&mut self` directly — no pinning needed.
    while let Some(chunk) = field
        .chunk()
        .await
        .map_err(|e| HttpException::new(400, format!("Multipart read error: {e}")))?
    {
        buf.extend_from_slice(&chunk);
        // Sentinel: as soon as we have read more than `limit` bytes we know the
        // upload is over cap — mirror Python's `len(data) > limit` check and
        // return 413 immediately (don't drain the rest of the body).
        if buf.len() > limit {
            return Err(HttpException::new(
                413,
                format!("{label} exceeds {} limit", format_byte_limit(limit)),
            ));
        }
    }

    Ok(buf)
}

// ── tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    // ── format_byte_limit ───────────────────────────────────────────────────────

    /// Exact-MB boundary → "X MB".
    #[test]
    fn fmt_mb() {
        assert_eq!(format_byte_limit(1024 * 1024), "1 MB");
        assert_eq!(format_byte_limit(25 * 1024 * 1024), "25 MB");
        assert_eq!(format_byte_limit(100 * 1024 * 1024), "100 MB");
    }

    /// Exact-KB boundary (not a full MB) → "X KB".
    #[test]
    fn fmt_kb() {
        assert_eq!(format_byte_limit(1024), "1 KB");
        assert_eq!(format_byte_limit(512 * 1024), "512 KB");
        // 1.5 MB is not a whole-MB multiple but is a whole-KB multiple.
        assert_eq!(format_byte_limit(1536 * 1024), "1536 KB");
    }

    /// Arbitrary byte count → "X bytes".
    #[test]
    fn fmt_bytes() {
        // 0 % (1024*1024) == 0, so Python returns "0 MB" — we match.
        assert_eq!(format_byte_limit(0), "0 MB");
        assert_eq!(format_byte_limit(1), "1 bytes");
        assert_eq!(format_byte_limit(500), "500 bytes");
    }

    // ── read_upload_limited via a round-trip axum multipart request ─────────────
    //
    // We build a real `multipart/form-data` request body, let axum parse it, and
    // pass the extracted `Field` to `read_upload_limited`. This exercises the full
    // code path without any mocking.

    use axum::body::Body;
    use axum::extract::Multipart;
    use axum::http::Request;

    /// Build a raw `multipart/form-data` body with a single `file` field containing
    /// `data`. Returns `(content_type_header, body_bytes)`.
    fn make_multipart_body(data: &[u8]) -> (String, Vec<u8>) {
        let boundary = "testboundary1234567890";
        let ct = format!("multipart/form-data; boundary={boundary}");
        let mut body = Vec::new();
        body.extend_from_slice(format!("--{boundary}\r\n").as_bytes());
        body.extend_from_slice(
            b"Content-Disposition: form-data; name=\"file\"; filename=\"test.bin\"\r\n",
        );
        body.extend_from_slice(b"Content-Type: application/octet-stream\r\n\r\n");
        body.extend_from_slice(data);
        body.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());
        (ct, body)
    }

    /// Helper: run `read_upload_limited` on a synthetic field with the given bytes.
    async fn run_limited(data: &[u8], limit: usize) -> Result<Vec<u8>, HttpException> {
        let (ct, body) = make_multipart_body(data);
        let req = Request::builder()
            .header("content-type", ct)
            .body(Body::from(body))
            .unwrap();
        let mut mp = Multipart::from_request(req, &()).await.unwrap();
        let field = mp.next_field().await.unwrap().expect("field present");
        read_upload_limited(field, limit, "Upload").await
    }

    /// Under-limit upload → Ok with the exact bytes.
    #[tokio::test]
    async fn under_limit_returns_bytes() {
        let data = b"hello world";
        let result = run_limited(data, 100).await;
        assert_eq!(result.unwrap(), data);
    }

    /// Exactly at limit → Ok (len == limit is not over).
    #[tokio::test]
    async fn at_limit_ok() {
        let data: Vec<u8> = vec![0xAB; 10];
        let result = run_limited(&data, 10).await;
        assert_eq!(result.unwrap(), data);
    }

    /// One byte over limit → 413.
    #[tokio::test]
    async fn over_limit_413() {
        let data: Vec<u8> = vec![0xFF; 11];
        let result = run_limited(&data, 10).await;
        let err = result.unwrap_err();
        assert_eq!(err.status_code, 413);
        assert!(
            err.detail.contains("exceeds"),
            "detail should contain 'exceeds': {}",
            err.detail
        );
        assert!(
            err.detail.contains("10 bytes"),
            "detail should contain formatted limit: {}",
            err.detail
        );
    }

    /// Error message uses the caller-supplied label.
    #[tokio::test]
    async fn label_in_error_message() {
        let (ct, body) = make_multipart_body(&[0u8; 20]);
        let req = Request::builder()
            .header("content-type", ct)
            .body(Body::from(body))
            .unwrap();
        let mut mp = Multipart::from_request(req, &()).await.unwrap();
        let field = mp.next_field().await.unwrap().expect("field present");
        let err = read_upload_limited(field, 5, "ICS file").await.unwrap_err();
        assert_eq!(err.status_code, 413);
        assert!(
            err.detail.starts_with("ICS file exceeds"),
            "detail should start with label: {}",
            err.detail
        );
    }

    /// MB-formatted limit appears in the 413 detail. Uses a 1 MB cap so the body
    /// stays under axum's 2 MB default Multipart limit (the live routes disable
    /// that limit via build_router); MB formatting is identical for any N MB.
    #[tokio::test]
    async fn mb_limit_format_in_detail() {
        const CAP: usize = 1024 * 1024;
        let data: Vec<u8> = vec![0u8; CAP + 1];
        let (ct, body) = make_multipart_body(&data);
        let req = Request::builder()
            .header("content-type", ct)
            .body(Body::from(body))
            .unwrap();
        let mut mp = Multipart::from_request(req, &()).await.unwrap();
        let field = mp.next_field().await.unwrap().expect("field present");
        let err = read_upload_limited(field, CAP, "Attachment")
            .await
            .unwrap_err();
        assert_eq!(err.status_code, 413);
        assert!(
            err.detail.contains("1 MB"),
            "detail should contain '1 MB': {}",
            err.detail
        );
    }

    // The `Multipart::from_request` extractor is imported here; in route handlers
    // it arrives via the axum extractor machinery, but for test purposes we drive
    // it directly. This is the same pattern used in stt_routes tests.
    use axum::extract::FromRequest;
}
