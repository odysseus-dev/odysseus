// routes/emoji_routes.rs  <- routes/emoji_routes.py
//! Same-origin emoji SVG proxy. The frontend rewrites emoji in chat to a
//!   `<span class="emoji" style="--em:url('/api/emoji/<codepoints>.svg')">`
//! which uses the returned SVG as a CSS mask tinted to the text color, so emoji
//! render as monochrome line icons (project rule: never colorful emoji). The
//! black line-art SVGs are lazily fetched from the OpenMoji CDN on first use and
//! cached on disk, so:
//!   - the client only ever talks to our own origin (no CDN dep, no CSP change),
//!   - the repo isn't bloated with thousands of SVG files,
//!   - it works offline once an emoji has been seen once.
//! Unknown/unreachable codepoints return a transparent SVG (not 404), so the CSS
//! mask shows nothing rather than a solid currentColor box.
//!
//! Python factory:
//!
//! ```python
//! def setup_emoji_routes() -> APIRouter:
//!     router = APIRouter(prefix="/api/emoji", tags=["emoji"])
//!
//!     def _blank() -> Response:
//!         return Response(_BLANK_SVG, media_type="image/svg+xml", headers=_BLANK_HEADERS)
//!
//!     @router.get("/{code}.svg")
//!     async def emoji_svg(code: str): ...
//! ```
//!
//! No auth, no DB, no managers — pure FS cache + one outbound `reqwest` GET, so
//! the module is `web`-gated only. `setup_emoji_routes()` takes no parameters,
//! matching the Python factory (which already captured no deps).

use std::path::PathBuf;

use axum::body::Body;
use axum::extract::Path;
use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::Router;
use once_cell::sync::Lazy;
use regex::Regex;

use crate::routes::AppState;

/// `_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "emoji_cache"`.
///
/// In Python this is resolved relative to the *source file*, i.e. the project
/// root's `data/` directory — independent of the process CWD. The faithful Rust
/// analogue is therefore [`crate::src::constants::DATA_DIR`] (the
/// manifest-parent `data/`, not a CWD-relative `data/`) joined with
/// `emoji_cache`.
fn cache_dir() -> PathBuf {
    PathBuf::from(&*crate::src::constants::DATA_DIR).join("emoji_cache")
}

/// `_OPENMOJI_BASE = "https://cdn.jsdelivr.net/npm/openmoji@15.0.0/black/svg"`.
///
/// OpenMoji "black" set = monochrome line-art SVGs. Filenames are the codepoints
/// in UPPERCASE (FE0F dropped, same as we compute), '-' joined.
const OPENMOJI_BASE: &str = "https://cdn.jsdelivr.net/npm/openmoji@15.0.0/black/svg";

/// `_CODE_RE = re.compile(r"^[0-9a-f]{2,6}(?:-[0-9a-f]{2,6})*$")`.
///
/// codepoints like "1f600" or "1f468-200d-1f469-200d-1f467"
/// (lowercase hex, '-' joined).
static CODE_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[0-9a-f]{2,6}(?:-[0-9a-f]{2,6})*$").unwrap());

/// `_SVG_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}`.
const SVG_CACHE_CONTROL: &str = "public, max-age=31536000, immutable";

/// `_BLANK_SVG` — returned when a codepoint is unknown/unreachable: an empty
/// (transparent) SVG, so the CSS mask renders nothing instead of a solid box.
/// Not cached, so a later request can still pick up the real glyph once the CDN
/// is reachable.
const BLANK_SVG: &[u8] = br#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"></svg>"#;

/// `_BLANK_HEADERS = {"Cache-Control": "no-store"}`.
const BLANK_CACHE_CONTROL: &str = "no-store";

/// `def _blank() -> Response:` —
/// `return Response(_BLANK_SVG, media_type="image/svg+xml", headers=_BLANK_HEADERS)`.
///
/// Infallible: a `Response` built from static bytes with two fixed headers never
/// fails to construct, so we `unwrap()` (mirroring the Python which cannot raise
/// here either).
fn blank() -> Response {
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "image/svg+xml")
        .header(header::CACHE_CONTROL, BLANK_CACHE_CONTROL)
        .body(Body::from(BLANK_SVG))
        .unwrap()
}

/// `Response(content, media_type="image/svg+xml", headers=_SVG_HEADERS)` /
/// `FileResponse(fp, media_type="image/svg+xml", headers=_SVG_HEADERS)`.
///
/// Both the cache-hit (`FileResponse`) and CDN-hit (`Response`) branches emit the
/// identical body + `image/svg+xml` content type + the long-lived immutable
/// `Cache-Control`, so they share one builder.
fn svg_response(content: Vec<u8>) -> Response {
    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "image/svg+xml")
        .header(header::CACHE_CONTROL, SVG_CACHE_CONTROL)
        .body(Body::from(content))
        .unwrap()
}

/// `@router.get("/{code}.svg")` — `async def emoji_svg(code: str):`
///
/// Lazily resolve the monochrome OpenMoji SVG for `code`, caching it on disk.
/// Returns the transparent [`blank`] SVG for any unknown/invalid/unreachable
/// codepoint (never an error response), so the CSS mask renders nothing.
///
/// ## `.svg` suffix handling
/// FastAPI mounts the literal-suffix path `/{code}.svg`, so `code` is the part
/// *before* `.svg` and a request without the suffix does not match the route
/// (FastAPI 404s). axum's `:code` capture grabs the whole final segment, so we
/// strip a trailing `.svg` here; a request that lacks it never matched the
/// Python route and so returns `404 Not Found` to preserve that behaviour.
async fn emoji_svg(Path(code): Path<String>) -> Response {
    // Mirror FastAPI's literal `.svg` suffix: only `/{code}.svg` matches the
    // Python route. Strip it; a path without `.svg` was a non-match (404) there.
    let code = match code.strip_suffix(".svg") {
        Some(c) => c,
        None => return (StatusCode::NOT_FOUND, "Not Found").into_response(),
    };

    // code = code.lower()
    let code = code.to_lowercase();

    // if not _CODE_RE.match(code): return _blank()
    if !CODE_RE.is_match(&code) {
        return blank();
    }

    // _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    let dir = cache_dir();
    let _ = std::fs::create_dir_all(&dir);

    // fp = _CACHE_DIR / f"{code}.svg"
    let fp = dir.join(format!("{code}.svg"));

    // if fp.exists(): return FileResponse(fp, media_type=..., headers=_SVG_HEADERS)
    if fp.exists() {
        if let Ok(bytes) = std::fs::read(&fp) {
            return svg_response(bytes);
        }
        // A stat-able-but-unreadable cache file is not something the Python ever
        // hits (FileResponse would itself error); fall through to a CDN refetch.
    }

    // First time we've seen this emoji — fetch the OpenMoji black SVG + cache it.
    // OpenMoji filenames are the codepoints uppercased.
    //
    //   try:
    //       async with httpx.AsyncClient(timeout=8.0) as client:
    //           r = await client.get(f"{_OPENMOJI_BASE}/{code.upper()}.svg")
    //       if r.status_code == 200 and b"<svg" in r.content[:256]:
    //           try: fp.write_bytes(r.content)
    //           except Exception: pass  # cache write is best-effort
    //           return Response(r.content, media_type=..., headers=_SVG_HEADERS)
    //   except Exception as e:
    //       logger.warning("emoji fetch %s failed: %s", code, e)
    let url = format!("{}/{}.svg", OPENMOJI_BASE, code.to_uppercase());
    match fetch_openmoji(&url).await {
        Ok((status, content)) => {
            // r.status_code == 200 and b"<svg" in r.content[:256]
            if status == 200 && contains_svg_marker(&content) {
                // cache write is best-effort (Python wraps in try/except: pass)
                let _ = std::fs::write(&fp, &content);
                return svg_response(content);
            }
        }
        Err(e) => {
            // logger.warning("emoji fetch %s failed: %s", code, e)
            crate::pylog::warning(&format!("emoji fetch {code} failed: {e}"));
        }
    }

    // return _blank()
    blank()
}

/// `async with httpx.AsyncClient(timeout=8.0) as client: r = await client.get(url)`.
///
/// Returns `(status_code, body_bytes)` on a completed response, or the transport
/// error so the caller can log it and fall back to the blank SVG (mirroring the
/// Python `except Exception` arm). The 8-second timeout matches `timeout=8.0`.
async fn fetch_openmoji(url: &str) -> Result<(u16, Vec<u8>), reqwest::Error> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(8))
        .build()?;
    let resp = client.get(url).send().await?;
    let status = resp.status().as_u16();
    let bytes = resp.bytes().await?;
    Ok((status, bytes.to_vec()))
}

/// `b"<svg" in r.content[:256]` — the OpenMoji-vs-CDN-error-page guard.
///
/// jsdelivr serves a 200 HTML error page for unknown packages/files, so the
/// Python checks the first 256 bytes contain the literal `<svg` opening tag
/// before trusting (and caching) the body.
fn contains_svg_marker(content: &[u8]) -> bool {
    let head = &content[..content.len().min(256)];
    head.windows(4).any(|w| w == b"<svg")
}

/// `setup_emoji_routes() -> APIRouter` —
/// `router = APIRouter(prefix="/api/emoji", tags=["emoji"])`.
///
/// The single handler is mounted at the absolute path the FastAPI
/// `APIRouter(prefix="/api/emoji")` + `@router.get("/{code}.svg")` yields:
/// `/api/emoji/:code` (the `.svg` suffix is matched/stripped inside the handler;
/// see [`emoji_svg`]). Deps come from `State<AppState>` per the foundation, but
/// this router needs none, so the handler ignores it.
pub fn setup_emoji_routes() -> Router<AppState> {
    Router::new().route("/api/emoji/:code", get(emoji_svg))
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

    #[test]
    fn code_regex_matches_python() {
        // Valid: single + ZWJ-joined sequences, lowercase hex, 2..6 hex per group.
        assert!(CODE_RE.is_match("1f600"));
        assert!(CODE_RE.is_match("1f468-200d-1f469-200d-1f467"));
        assert!(CODE_RE.is_match("2764")); // 4 hex
        assert!(CODE_RE.is_match("ab")); // min 2 hex
        assert!(CODE_RE.is_match("1f3f4-e0067-e0062-e0073")); // 6-hex tag groups
        // Invalid: uppercase (handled by .lower() before match, but the regex
        // itself rejects it), too short/long groups, stray chars, trailing dot.
        assert!(!CODE_RE.is_match("1F600")); // uppercase
        assert!(!CODE_RE.is_match("g")); // non-hex + too short
        assert!(!CODE_RE.is_match("1")); // 1 hex (min is 2)
        assert!(!CODE_RE.is_match("1f6000000")); // 9 hex (max group is 6)
        assert!(!CODE_RE.is_match("1f600-")); // trailing dash
        assert!(!CODE_RE.is_match("1f600.svg")); // dot not allowed
        assert!(!CODE_RE.is_match("")); // empty
    }

    #[test]
    fn svg_marker_only_in_first_256_bytes() {
        // Present near the front -> trusted.
        assert!(contains_svg_marker(br#"<svg xmlns="...">"#));
        // Present but past byte 256 -> not trusted (mirrors r.content[:256]).
        let mut buf = vec![b' '; 300];
        buf.extend_from_slice(b"<svg>");
        assert!(!contains_svg_marker(&buf));
        // Absent (jsdelivr HTML error page) -> not trusted.
        assert!(!contains_svg_marker(b"<!DOCTYPE html><html>Cannot find</html>"));
        // Short body still scanned without panicking.
        assert!(!contains_svg_marker(b"x"));
        assert!(contains_svg_marker(b"<svg"));
    }

    #[tokio::test]
    async fn blank_svg_body_and_headers() {
        let resp = blank();
        assert_eq!(resp.status(), StatusCode::OK);
        assert_eq!(
            resp.headers().get(header::CONTENT_TYPE).unwrap(),
            "image/svg+xml"
        );
        assert_eq!(
            resp.headers().get(header::CACHE_CONTROL).unwrap(),
            "no-store"
        );
        assert_eq!(body_bytes(resp).await, BLANK_SVG);
    }

    #[tokio::test]
    async fn svg_response_carries_immutable_cache_control() {
        let resp = svg_response(b"<svg>cached</svg>".to_vec());
        assert_eq!(resp.status(), StatusCode::OK);
        assert_eq!(
            resp.headers().get(header::CONTENT_TYPE).unwrap(),
            "image/svg+xml"
        );
        assert_eq!(
            resp.headers().get(header::CACHE_CONTROL).unwrap(),
            "public, max-age=31536000, immutable"
        );
        assert_eq!(body_bytes(resp).await, b"<svg>cached</svg>");
    }

    #[tokio::test]
    async fn invalid_code_returns_blank_no_store() {
        // `g` is non-hex -> regex miss -> _blank() (transparent, no-store), 200.
        let resp = emoji_svg(Path("g.svg".to_string())).await;
        assert_eq!(resp.status(), StatusCode::OK);
        assert_eq!(
            resp.headers().get(header::CACHE_CONTROL).unwrap(),
            "no-store"
        );
        assert_eq!(body_bytes(resp).await, BLANK_SVG);
    }

    #[tokio::test]
    async fn missing_svg_suffix_is_not_found() {
        // FastAPI mounts `/{code}.svg`; a request without `.svg` never matched
        // that route (404). We preserve that.
        let resp = emoji_svg(Path("1f600".to_string())).await;
        assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    }

    #[test]
    fn cache_dir_is_project_data_not_cwd() {
        // Faithful to Python's `__file__`-relative resolution: the cache lives
        // under the manifest-parent `data/`, not a CWD-relative `data/`.
        let dir = cache_dir();
        assert!(dir.ends_with("emoji_cache"));
        assert!(dir
            .to_string_lossy()
            .contains(&*crate::src::constants::DATA_DIR));
    }
}
