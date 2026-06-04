// routes/auth_adapter.rs  <- src/auth_helpers.py + routes/email_helpers.py (auth surface) + core/middleware.require_admin
//! The axum auth-adapter (foundation unit F3).
//!
//! The ported route handlers mirror the FastAPI `Depends(require_user / require_owner /
//! require_admin / require_privilege)` shape. In Python those dependencies operate on a
//! `Request` (reading `request.state.current_user`, `request.app.state.auth_manager`, and
//! `request.client.host`); in axum the same three facts arrive as an
//! `Extension<CurrentUser>` (stamped by the auth gate only when resolved), the shared
//! [`AppState`] (`state.auth`), and a `client_host` derived from `ConnectInfo<SocketAddr>`.
//!
//! This module **bridges** that gap rather than re-implementing the logic: it constructs the
//! already-ported [`crate::src::auth_helpers::Request`] stand-in and delegates to the
//! already-ported `require_user` / `require_privilege`, then maps the resulting
//! [`PyError`] to an [`HttpException`] exactly as FastAPI would surface the raised
//! `HTTPException`. `require_admin` wraps the already-ported
//! [`crate::core::middleware::require_admin`]. `require_owner` / `_assert_owns_account`
//! port the `routes/email_helpers.py` ownership surface (raw `rusqlite` on `email_accounts`),
//! and `verify_session_owner` ports `routes/session_routes.py::_verify_session_owner`.
//!
//! These are plain async-callable helper fns invoked at the top of each handler — the
//! explicit-call analogue of `Depends(...)` — NOT `FromRequest` extractors. That matches the
//! closure-capture Python shape and avoids extractor-ordering pitfalls with `Multipart`/`Query`.

use crate::error::PyError;
use crate::routes::{AppState, HttpException};
use crate::src::auth_helpers as helpers;

/// Build the [`helpers::Request`] stand-in the ported auth helpers read.
///
/// `current_user` is `request.state.current_user` (`None` when the auth gate could not
/// resolve a user); `auth_manager` is `request.app.state.auth_manager` (always wired here,
/// so `Some`); `client_host` is `request.client.host` (from `ConnectInfo<SocketAddr>`).
fn build_request<'a>(
    user: Option<&str>,
    state: &'a AppState,
    client_host: Option<&str>,
) -> helpers::Request<'a> {
    helpers::Request {
        current_user: user.map(str::to_string),
        // `request.app.state.auth_manager` is always wired in the axum app.
        auth_manager: Some(&*state.auth),
        client_host: client_host.map(str::to_string),
    }
}

/// Map a raised [`PyError`] from the auth helpers to the [`HttpException`] FastAPI would
/// surface. The helpers only ever raise `HTTPException(401, "Not authenticated")`
/// (`PyError::Other`) or `HTTPException(403, "...")` (`PyError::Permission`); any other
/// variant is impossible on this path, so it maps to a defensive 401.
fn map_err(e: PyError) -> HttpException {
    match e {
        // `raise HTTPException(403, f"Your account is not allowed to ...")`
        PyError::Permission(msg) => HttpException::new(403, msg),
        // `raise HTTPException(401, "Not authenticated")`
        PyError::Other(msg) => HttpException::new(401, msg),
        // The helpers never raise anything else; fail closed at 401.
        other => HttpException::new(401, other.to_string()),
    }
}

/// `Depends(require_user)` — reject unauthenticated callers, even if upstream middleware was
/// bypassed (`LOCALHOST_BYPASS`, `AUTH_ENABLED=false`, SSRF from a sibling service). Returns
/// the resolved username, or `""` in unconfigured first-run mode when the caller is on
/// loopback.
///
/// Delegates to [`helpers::require_user`]; `PyError::Other("Not authenticated")` ->
/// `HttpException::new(401, "Not authenticated")`.
pub fn require_user(
    user: Option<&str>,
    state: &AppState,
    client_host: Option<&str>,
) -> Result<String, HttpException> {
    let req = build_request(user, state, client_host);
    helpers::require_user(&req).map_err(map_err)
}

/// `Depends(lambda r: require_privilege(r, key))` — reject callers whose `auth.json`
/// privilege flag for `key` is False. Returns the username so the handler can keep using it.
///
/// Delegates to [`helpers::require_privilege`]; `PyError::Permission(msg)` ->
/// `HttpException::new(403, msg)`, `PyError::Other` -> `HttpException::new(401, ...)`.
pub fn require_privilege(
    user: Option<&str>,
    state: &AppState,
    client_host: Option<&str>,
    key: &str,
) -> Result<String, HttpException> {
    let req = build_request(user, state, client_host);
    helpers::require_privilege(&req, key).map_err(map_err)
}

/// `Depends(require_admin)` (core/middleware.py) — `Ok(())` to allow, else
/// `HttpException::new(403, "Admin only")`.
///
/// Wraps the already-ported [`crate::core::middleware::require_admin`], passing the resolved
/// inputs: the optional `X-Odysseus-Internal-Token` header value (`internal_header`), the
/// stamped `current_user`, plus `state.auth` / `state.auth_enabled`. The Python raises
/// `HTTPException(403, "Admin only")` on the `Err(())` arm.
pub fn require_admin(
    state: &AppState,
    internal_header: Option<&str>,
    user: Option<&str>,
) -> Result<(), HttpException> {
    crate::core::middleware::require_admin(&state.auth, state.auth_enabled, internal_header, user)
        .map_err(|()| HttpException::new(403, "Admin only"))
}

/// `Depends(require_owner)` (routes/email_helpers.py) — authenticate the caller and, if
/// `account_id` is present, assert ownership. Returns the resolved owner (`""` in
/// unconfigured single-user mode).
///
/// `owner = _require_auth(request)` (here [`require_user`], whose
/// `routes/email_helpers._require_auth` is byte-for-byte `src/auth_helpers.require_user`);
/// `if account_id: _assert_owns_account(account_id, owner)`.
pub fn require_owner(
    user: Option<&str>,
    state: &AppState,
    account_id: Option<&str>,
    client_host: Option<&str>,
) -> Result<String, HttpException> {
    let owner = require_user(user, state, client_host)?;
    if let Some(account_id) = account_id {
        if !account_id.is_empty() {
            assert_owns_account(account_id, &owner)?;
        }
    }
    Ok(owner)
}

/// `_assert_owns_account(account_id, owner)` (routes/email_helpers.py) — reject requests that
/// name an `account_id` belonging to another user.
///
/// `owner == ""` is the unconfigured / single-user case — accept any account. A missing row
/// or a row owned by someone else is reported as **404** (`"Account not found"`, NOT 403, so
/// we don't leak existence). Any DB error fails **closed** as **503** (`"Account check
/// failed"`), so a DB hiccup can't let cross-tenant access slip through.
pub fn assert_owns_account(account_id: &str, owner: &str) -> Result<(), HttpException> {
    // `if not account_id or not owner: return`
    if account_id.is_empty() || owner.is_empty() {
        return Ok(());
    }
    // `from core.database import SessionLocal; db = _SL()`  /  `finally: db.close()`
    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(e) => {
            // Fail closed — a DB hiccup must not let cross-tenant access slip through.
            crate::pylog::error(&format!("Account-owner check failed: {e}"));
            return Err(HttpException::new(503, "Account check failed"));
        }
    };
    // `row = db.query(EmailAccount).filter(EmailAccount.id == account_id).first()`
    let row: Result<Option<Option<String>>, rusqlite::Error> = conn
        .query_row(
            "SELECT owner FROM email_accounts WHERE id = ?1",
            rusqlite::params![account_id],
            |r| r.get::<_, Option<String>>(0),
        )
        .map(Some)
        .or_else(|e| match e {
            rusqlite::Error::QueryReturnedNoRows => Ok(None),
            other => Err(other),
        });
    match row {
        // `if row is None: raise HTTPException(404, "Account not found")`
        Ok(None) => Err(HttpException::new(404, "Account not found")),
        Ok(Some(row_owner)) => {
            // `if row.owner and row.owner != owner: raise HTTPException(404, ...)`
            // Treat as 404 (not 403) so we don't leak existence.
            match row_owner.as_deref() {
                Some(o) if !o.is_empty() && o != owner => {
                    Err(HttpException::new(404, "Account not found"))
                }
                _ => Ok(()),
            }
        }
        // `except Exception as e: ... raise HTTPException(503, "Account check failed")`
        Err(e) => {
            crate::pylog::error(&format!("Account-owner check failed: {e}"));
            Err(HttpException::new(503, "Account check failed"))
        }
    }
}

/// `_verify_session_owner(request, session_id)` (routes/session_routes.py) — verify the
/// current user owns the session. Raises **403** (`"Authentication required"`) when there is
/// no current user, else **404** (`"Session {session_id} not found"`) when the session is
/// missing OR owned by someone else. Like the Python it returns nothing on success — it is a
/// pure guard (`-> Result<(), _>`); every caller invokes it as a discarded `?;` statement (or
/// `.is_ok()`), never consuming a session body.
///
/// Python does a DIRECT, side-effect-free DB read:
/// `db.query(DbSession.owner).filter(DbSession.id == session_id).first()`. That resolves ANY
/// session row by id with NO archived / message-count filter and NO `last_accessed` mutation,
/// so it sees archived / 0-message / cache-evicted rows that are on disk but not in the
/// in-memory cache. We mirror it with a raw `rusqlite` `SELECT owner FROM sessions WHERE id`
/// off a fresh `session_local()` connection — NOT the in-memory `peek_session` (which 404s on
/// any uncached row, breaking unarchive) and NOT the hydrating `get_session` (which bumps
/// `last_accessed`). This shared foundation helper feeds the session / history / chat
/// clusters, so the bump-free direct-DB read is the correct, Python-faithful resolver for all.
///
/// NOTE: this mirrors `_verify_session_owner` exactly, which is STRICTER than the inline
/// `web/mod.rs::owns()` predicate (the latter also treats a `null`/empty owner as owned).
/// Python's `if not row` is true only when there is NO row; an existing row with `owner = NULL`
/// is still truthy, so it reaches `row.owner != user` (`None != "<user>"`) and 404s. A
/// `null`-owner session is therefore *not* owned by anyone here — the Python behavior for the
/// session/chat/history clusters.
pub fn verify_session_owner(
    state: &AppState,
    user: Option<&str>,
    session_id: &str,
) -> Result<(), HttpException> {
    // `state` is unused now that the check reads the DB directly (Python opens its own
    // `SessionLocal()` rather than touching the in-memory manager) — kept in the signature
    // so every call site stays byte-for-byte identical with the rest of the auth-adapter.
    let _ = state;
    // `user = get_current_user(request); if not user: raise HTTPException(403, ...)`
    let user = match user {
        Some(u) if !u.is_empty() => u,
        _ => return Err(HttpException::new(403, "Authentication required")),
    };
    // `db = SessionLocal()` / `finally: db.close()` — a fresh connection, dropped on return.
    // Python has no try/except here: a connection or query failure propagates as an unhandled
    // exception (FastAPI 500), so we surface 500 too rather than masking it as a 404.
    let conn = match crate::core::database::session_local() {
        Ok(c) => c,
        Err(e) => {
            crate::pylog::error(&format!("Session-owner check failed: {e}"));
            return Err(HttpException::new(500, "Internal Server Error"));
        }
    };
    // `row = db.query(DbSession.owner).filter(DbSession.id == session_id).first()`
    use rusqlite::OptionalExtension;
    let row: Option<Option<String>> = match conn
        .query_row(
            "SELECT owner FROM sessions WHERE id = ?1",
            rusqlite::params![session_id],
            |r| r.get::<_, Option<String>>(0),
        )
        .optional()
    {
        Ok(r) => r,
        Err(e) => {
            crate::pylog::error(&format!("Session-owner check failed: {e}"));
            return Err(HttpException::new(500, "Internal Server Error"));
        }
    };
    match row {
        // `if not row: raise HTTPException(404, f"Session {session_id} not found")`
        None => Err(HttpException::new(
            404,
            format!("Session {session_id} not found"),
        )),
        // `if row.owner != user: raise HTTPException(404, f"Session {session_id} not found")`
        // `owner` may be NULL (the legacy / shared row) — `None != Some(user)` -> 404.
        Some(owner) => {
            if owner.as_deref() == Some(user) {
                Ok(())
            } else {
                Err(HttpException::new(
                    404,
                    format!("Session {session_id} not found"),
                ))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::auth::AuthManager;
    use std::sync::Arc;

    /// A bare-bones [`AppState`] for adapter tests: only `auth` / `auth_enabled` /
    /// `localhost_bypass` are consulted by these helpers; the manager handles are
    /// irrelevant here, so we leave constructing the full state to the integration
    /// tests and build the helper [`helpers::Request`] directly where AppState is not
    /// strictly required.
    fn auth_mgr() -> Arc<AuthManager> {
        // A fresh, unconfigured AuthManager points at the active DATA_DIR's auth.json.
        Arc::new(AuthManager::new())
    }

    #[test]
    fn require_user_unconfigured_loopback_returns_empty() {
        // No current user + unconfigured auth + loopback host -> "" (first-run mode).
        let auth = auth_mgr();
        // Build the helpers::Request directly (the adapter's build_request shape).
        let req = helpers::Request {
            current_user: None,
            auth_manager: Some(&*auth),
            client_host: Some("127.0.0.1".to_string()),
        };
        // Only meaningful when the test data dir is genuinely unconfigured.
        if !auth.is_configured() {
            assert_eq!(helpers::require_user(&req).map_err(map_err).unwrap(), "");
        }
    }

    #[test]
    fn map_err_maps_permission_to_403_and_other_to_401() {
        let p = map_err(PyError::permission("Your account is not allowed to x."));
        assert_eq!(p.status_code, 403);
        assert_eq!(p.detail, "Your account is not allowed to x.");

        let o = map_err(PyError::other("Not authenticated"));
        assert_eq!(o.status_code, 401);
        assert_eq!(o.detail, "Not authenticated");
    }

    #[test]
    fn require_user_unconfigured_nonloopback_raises_401() {
        let auth = auth_mgr();
        let req = helpers::Request {
            current_user: None,
            auth_manager: Some(&*auth),
            client_host: Some("10.0.0.5".to_string()),
        };
        if !auth.is_configured() {
            let e = helpers::require_user(&req).map_err(map_err).unwrap_err();
            assert_eq!(e.status_code, 401);
            assert_eq!(e.detail, "Not authenticated");
        }
    }
}
