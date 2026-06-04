// core/middleware.rs  <- core/middleware.py
//! Shared middleware, decorators, and request helpers.
//!
//! The Starlette `BaseHTTPMiddleware` classes become axum `from_fn` middleware
//! in `crate::web`; this module holds the framework-independent pieces the
//! Python keeps here: the internal-tool token/header, the `require_admin` gate,
//! and the per-response security-header set computed by
//! `SecurityHeadersMiddleware.dispatch`.

use crate::core::auth::AuthManager;
use crate::pyos as os;
use crate::pysecrets as secrets;
use once_cell::sync::Lazy;

/// Per-process token that lets the in-app tool layer hit admin-gated routes via
/// HTTP loopback (the agent's tool calls don't carry the admin user's session
/// cookie). Set once at import; never persisted or exposed externally.
pub static INTERNAL_TOOL_TOKEN: Lazy<String> =
    Lazy::new(|| os::getenv_opt("ODYSSEUS_INTERNAL_TOKEN").unwrap_or_else(|| secrets::token_hex(32)));
pub const INTERNAL_TOOL_HEADER: &str = "X-Odysseus-Internal-Token";

/// Constant-time byte-string equality, mirroring `secrets.compare_digest`.
///
/// Compares `a` and `b` without short-circuiting on the first mismatching byte,
/// so the running time does not leak how many leading bytes matched. Like
/// `compare_digest`, a length mismatch yields `false`; the XOR-accumulate loop
/// still runs over the shorter input so the comparison stays branch-balanced.
/// `subtle`/`constant_time_eq` aren't dependencies, hence this small local impl.
fn ct_eq(a: &[u8], b: &[u8]) -> bool {
    // `compare_digest` returns False on differing lengths. Fold the length
    // difference into the accumulator instead of returning early so the loop
    // body executes identically regardless of where bytes diverge. Collapse the
    // 64-bit length XOR into a single byte so any differing bit forces a mismatch.
    let len_xor = a.len() as u64 ^ b.len() as u64;
    let mut diff: u8 = 0;
    for shift in (0..64).step_by(8) {
        diff |= (len_xor >> shift) as u8;
    }
    let n = a.len().min(b.len());
    for i in 0..n {
        diff |= a[i] ^ b[i];
    }
    diff == 0
}

/// One response header: `(name, value)`.
pub type Header = (&'static str, String);

/// `require_admin(request)` — `Ok(())` to allow, `Err(())` to raise 403.
///
/// Translated to take the resolved inputs the Python pulls off the request:
/// the optional internal-tool header value, the stamped `current_user`, the
/// `auth_manager`, and the `AUTH_ENABLED` flag.
// `Err(())` mirrors Python `raise HTTPException(403)` with no body detail — the
// unit error is the faithful payload-less mapping (callers turn it into 403).
#[allow(clippy::result_unit_err)]
pub fn require_admin(
    auth: &AuthManager,
    auth_enabled: bool,
    internal_header_value: Option<&str>,
    current_user: Option<&str>,
) -> Result<(), ()> {
    // In-process bypass for tool-layer loopback calls (header-direct or the
    // auth middleware already stamped current_user = "internal-tool").
    // `hdr and secrets.compare_digest(hdr, TOKEN)`: only a present, non-empty
    // header is compared, and the comparison is constant-time to resist timing
    // side-channels on the token.
    if let Some(hdr) = internal_header_value {
        if !hdr.is_empty() && ct_eq(hdr.as_bytes(), INTERNAL_TOOL_TOKEN.as_bytes()) {
            return Ok(());
        }
    }
    if current_user == Some("internal-tool") {
        return Ok(());
    }
    if !auth_enabled {
        return Ok(());
    }
    if !auth.is_configured() {
        return Err(());
    }
    match current_user {
        Some(u) if auth.is_admin(u) => Ok(()),
        _ => Err(()),
    }
}

/// `SecurityHeadersMiddleware.dispatch` — the security headers for a response,
/// given the request path and the per-request CSP nonce. Reproduces the three
/// branches (self-contained report pages, sandboxed tool-render iframes, and the
/// default app pages) verbatim.
pub fn security_headers(path: &str, nonce: &str) -> Vec<Header> {
    // Tool render endpoints are served inside iframes — allow framing by self.
    let is_tool_render = path.starts_with("/api/tools/") && path.ends_with("/render");
    // Visual report pages are self-contained HTML — need inline scripts + images.
    let is_report = path.starts_with("/api/research/report/");

    let mut headers: Vec<Header> = vec![
        ("X-Content-Type-Options", "nosniff".to_string()),
        ("Referrer-Policy", "no-referrer".to_string()),
    ];

    if is_report {
        headers.push((
            "Content-Security-Policy",
            "default-src 'self'; \
             script-src 'self' 'unsafe-inline'; \
             style-src 'self' 'unsafe-inline'; \
             font-src 'self'; \
             img-src 'self' data: blob: https:; \
             connect-src 'self'; \
             frame-ancestors 'none'"
                .to_string(),
        ));
    } else if is_tool_render {
        // Tool iframe content: skip all framing headers — the iframe's
        // sandbox="allow-scripts" attribute provides isolation. Don't overwrite
        // the route's own restrictive CSP either.
    } else {
        headers.push(("X-Frame-Options", "DENY".to_string()));
        headers.push((
            "Content-Security-Policy",
            format!(
                "default-src 'self'; \
                 script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net; \
                 style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; \
                 font-src 'self' https://cdn.jsdelivr.net; \
                 img-src 'self' data: blob:; \
                 media-src 'self' blob:; \
                 connect-src 'self'; \
                 frame-src 'self'; \
                 frame-ancestors 'none'"
            ),
        ));
    }
    headers
}
