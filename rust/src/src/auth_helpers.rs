// src/auth_helpers.rs  <- src/auth_helpers.py
//! Shared auth helpers used by all route files.

use crate::core::auth::AuthManager;
use crate::error::{PyError, PyResult};
use crate::pyos as os;

/// A faithful stand-in for the FastAPI `Request` object the helpers receive.
///
/// The Python reads three things off the request via `getattr`:
///   * `request.state.current_user`     — set by the auth middleware,
///   * `request.app.state.auth_manager` — the shared [`AuthManager`],
///   * `request.client.host`            — the peer address.
/// We model exactly that surface so the logic below reads line-by-line.
pub struct Request<'a> {
    /// `request.state.current_user` (`None` when middleware didn't set it).
    pub current_user: Option<String>,
    /// `request.app.state.auth_manager` (`None` when not wired up).
    pub auth_manager: Option<&'a AuthManager>,
    /// `request.client.host` (`None` when there is no client, e.g. test calls).
    pub client_host: Option<String>,
}

/// Get current username from request state (set by auth middleware).
pub fn get_current_user(request: &Request) -> Option<String> {
    // getattr(request.state, 'current_user', None)
    request.current_user.clone()
}

/// The real human behind the request, for ownership/attribution.
///
/// Cookie sessions resolve to the logged-in username. Bearer `ody_` callers
/// come through as the sandboxed pseudo-user `"api"` so they can't wander into
/// cookie/user routes by default, but their token was minted by, and belongs
/// to, a real owner stamped on `request.state.api_token_owner`. Routes that
/// should attribute a token's actions to that owner (sessions, chat history)
/// call this instead of [`get_current_user`], so a paired client sees and
/// creates the SAME data as the owner's desktop UI rather than a separate
/// `"api"`-owned silo.
///
/// For cookie sessions this is identical to [`get_current_user`], so swapping a
/// route over is a no-op for browser users. A bearer token with no owner falls
/// back to [`get_current_user`] (the `"api"` pseudo-user), so it never escalates.
///
/// `api_token` / `api_token_owner` are the two facts the auth gate stamps on
/// `request.state` (in the axum port, the `web::ApiToken` extension's `present`
/// / `owner` fields); they are threaded in explicitly rather than carried on the
/// [`Request`] stand-in so the existing `require_user` / `require_privilege`
/// call sites keep their three-field `Request` literal unchanged.
//
// def effective_user(request):
//     if getattr(request.state, "api_token", False):
//         owner = getattr(request.state, "api_token_owner", None)
//         if owner:
//             return owner
//     return get_current_user(request)
pub fn effective_user(
    request: &Request,
    api_token: bool,
    api_token_owner: Option<&str>,
) -> Option<String> {
    // if getattr(request.state, "api_token", False):
    if api_token {
        // owner = getattr(request.state, "api_token_owner", None)
        // if owner: return owner  — `if owner` is falsy for both None and ""
        if let Some(owner) = api_token_owner {
            if !owner.is_empty() {
                return Some(owner.to_string());
            }
        }
    }
    // return get_current_user(request)
    get_current_user(request)
}

/// True when the operator has explicitly turned off auth via `.env`.
/// Mirrors the `AUTH_ENABLED` parse in `app.py` / `core/middleware.py` so the
/// three call sites agree on what "off" means.
//
// def _auth_disabled() -> bool:
//     return os.getenv("AUTH_ENABLED", "true").lower() == "false"
fn auth_disabled() -> bool {
    os::getenv("AUTH_ENABLED", "true").to_lowercase() == "false"
}

/// FastAPI dependency: reject unauthenticated callers when the upstream auth
/// middleware was bypassed unexpectedly (e.g. SSRF from a sibling service).
/// Returns the resolved username, or `""` in single-user / anonymous modes
/// where no username is available.
///
/// The three `""` cases are:
///   1. `AUTH_ENABLED=false` — the operator explicitly turned auth off. The
///      full `/login` flow is skipped (issue #622), so route-level
///      `require_user` must let the request through too instead of 401-ing and
///      forcing the browser to `/login`.
///   2. Unconfigured first-run + loopback caller — pre-setup access from
///      localhost so the operator can hit the SPA before creating the first
///      admin.
///   3. `LOCALHOST_BYPASS=true` + loopback caller — documented dev bypass.
///
/// Use this on routes that touch user data so middleware misconfig can't
/// open them up.
pub fn require_user(request: &Request) -> PyResult<String> {
    let u = get_current_user(request);
    if let Some(u) = u {
        if !u.is_empty() {
            return Ok(u);
        }
    }
    // Operator-disabled auth: honor it at the route layer too. Without this,
    // routes that depend on require_user 401, the front-end fetch wrapper
    // redirects to /login, and the user sees a login page despite
    // AUTH_ENABLED=false (issue #622). Docker / reverse-proxy deployments
    // hit this because requests arrive from a non-loopback client.host, so
    // the loopback fall-through below never fires.
    if auth_disabled() {
        return Ok(String::new());
    }
    let auth_mgr = request.auth_manager; // getattr(request.app.state, "auth_manager", None)
    let client = request.client_host.as_deref(); // getattr(request, "client", None)
    let host = client.unwrap_or(""); // (client.host if client else "") or ""
    let is_loopback = host == "127.0.0.1" || host == "::1" || host == "localhost";
    // LOCALHOST_BYPASS=true is the dev-only "I'm on loopback, skip auth"
    // switch. Mirror the middleware so routes don't 401 the same caller
    // the middleware just let through.
    if is_loopback && os::getenv("LOCALHOST_BYPASS", "false").to_lowercase() == "true" {
        return Ok(String::new());
    }
    if let Some(auth_mgr) = auth_mgr {
        if auth_mgr.is_configured() {
            // raise HTTPException(401, "Not authenticated")
            return Err(PyError::other("Not authenticated"));
        }
    }
    // Unconfigured / first-run mode: only allow loopback callers.
    if is_loopback {
        return Ok(String::new());
    }
    // raise HTTPException(401, "Not authenticated")
    Err(PyError::other("Not authenticated"))
}

/// Reject callers whose `auth.json` privilege flag for `key` is False.
/// Returns the username so the route handler can keep using it.
///
/// Admins always have every privilege via `auth_manager.get_privileges`
/// (which returns ADMIN_PRIVILEGES wholesale), so this is a no-op for
/// them. In unauthenticated single-user mode (`require_user` returns ""),
/// privileges aren't enforced.
pub fn require_privilege(request: &Request, key: &str) -> PyResult<String> {
    let user = require_user(request)?;
    if user.is_empty() {
        return Ok(user);
    }
    let auth_mgr = request.auth_manager; // getattr(request.app.state, "auth_manager", None)
    let auth_mgr = match auth_mgr {
        None => return Ok(user),
        Some(m) => m,
    };
    // try: privs = auth_mgr.get_privileges(user) or {}  / except Exception: return user
    // `get_privileges` is infallible in the Rust port, so the `except` arm is
    // unreachable; the merged map is never empty (defaults), so `or {}` is moot.
    // The upstream `if not isinstance(privs, dict): privs = {}` defensive cast is
    // also a no-op here: `get_privileges` returns `Map<String, Value>`, so `privs`
    // is statically always a dict and can never need the `{}` fallback.
    let privs = auth_mgr.get_privileges(&user);
    // True = permitted; missing key defaults to permitted (unknown privileges
    // fail open — the UI gates display-side).
    let permitted = privs.get(key).and_then(|v| v.as_bool()).unwrap_or(true);
    if !permitted {
        // raise HTTPException(403, f"Your account is not allowed to {key.replace('_', ' ')}.")
        return Err(PyError::permission(format!(
            "Your account is not allowed to {}.",
            key.replace('_', " ")
        )));
    }
    Ok(user)
}

/// Filter `query` so only rows owned by `user` (and optionally null-owner
/// 'shared' rows) come through. No-op when `user` is empty (single-user
/// mode). Returns the modified query.
///
// TODO(db): `query` is a SQLAlchemy `Query` and `model_cls` a mapped model;
// the `.filter(...)` chain with `model_cls.owner == user` / `== None` has no
// faithful analogue without pulling in a query builder. We mirror the control
// flow and leave the actual filtering to the DB layer once it exists.
pub fn owner_filter<Q>(query: Q, _model_cls: (), user: &str, include_shared: bool) -> Q {
    if user.is_empty() {
        return query;
    }
    if include_shared {
        // return query.filter((model_cls.owner == user) | (model_cls.owner == None))
        unimplemented!("owner_filter: SQLAlchemy query.filter (shared) — TODO(db)")
    }
    // return query.filter(model_cls.owner == user)
    unimplemented!("owner_filter: SQLAlchemy query.filter — TODO(db)")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::auth::AuthManager;

    /// A request stand-in with no resolved user and the given peer host.
    fn anon_req<'a>(auth: &'a AuthManager, host: Option<&str>) -> Request<'a> {
        Request {
            current_user: None,
            auth_manager: Some(auth),
            client_host: host.map(str::to_string),
        }
    }

    /// `effective_user` is a pure read of the threaded api-token facts + the
    /// stamped `current_user`; it touches no env, so it is safe to run in
    /// parallel with everything else.
    #[test]
    fn effective_user_prefers_token_owner_then_falls_back() {
        let auth = AuthManager::new();

        // Cookie session (not a token): identical to get_current_user.
        let cookie = Request {
            current_user: Some("alice".to_string()),
            auth_manager: Some(&auth),
            client_host: None,
        };
        assert_eq!(effective_user(&cookie, false, None), Some("alice".to_string()));
        // A token-owner on a non-token request is ignored (api_token=false).
        assert_eq!(
            effective_user(&cookie, false, Some("bob")),
            Some("alice".to_string())
        );

        // Bearer ody_ caller stamped as the "api" pseudo-user with a real owner:
        // attributed to the owner, not "api".
        let bearer = Request {
            current_user: Some("api".to_string()),
            auth_manager: Some(&auth),
            client_host: None,
        };
        assert_eq!(
            effective_user(&bearer, true, Some("bob")),
            Some("bob".to_string())
        );
        // Token with no owner (None) or an empty owner: never escalates — falls
        // back to get_current_user (the "api" pseudo-user). `if owner:` is falsy
        // for both None and "".
        assert_eq!(effective_user(&bearer, true, None), Some("api".to_string()));
        assert_eq!(effective_user(&bearer, true, Some("")), Some("api".to_string()));
    }

    /// `require_user`'s env-sensitive branches (AUTH_ENABLED=false, the
    /// LOCALHOST_BYPASS + loopback bypass) plus the unconfigured-loopback /
    /// non-loopback fall-throughs. Env vars are process-global, so all of these
    /// assertions live in ONE test (run serially within this thread) and the
    /// prior values are saved and restored around each mutation.
    #[test]
    fn require_user_env_modes() {
        let auth = AuthManager::new();

        let prev_auth = std::env::var("AUTH_ENABLED").ok();
        let prev_bypass = std::env::var("LOCALHOST_BYPASS").ok();

        let restore = |key: &str, prev: &Option<String>| match prev {
            Some(v) => std::env::set_var(key, v),
            None => std::env::remove_var(key),
        };

        // --- 1. AUTH_ENABLED=false: "" for any caller (even non-loopback),
        // short-circuiting before any is_configured check (issue #622). ---
        std::env::set_var("AUTH_ENABLED", "false");
        std::env::remove_var("LOCALHOST_BYPASS");
        assert_eq!(require_user(&anon_req(&auth, Some("10.0.0.5"))).unwrap(), "");
        assert_eq!(require_user(&anon_req(&auth, None)).unwrap(), "");
        // A resolved user still wins (returned before the disabled check).
        let mut configured = anon_req(&auth, Some("10.0.0.5"));
        configured.current_user = Some("alice".to_string());
        assert_eq!(require_user(&configured).unwrap(), "alice");

        // --- 2. LOCALHOST_BYPASS=true + loopback: "" even when configured. ---
        std::env::set_var("AUTH_ENABLED", "true");
        std::env::set_var("LOCALHOST_BYPASS", "true");
        assert_eq!(require_user(&anon_req(&auth, Some("127.0.0.1"))).unwrap(), "");
        assert_eq!(require_user(&anon_req(&auth, Some("::1"))).unwrap(), "");
        assert_eq!(require_user(&anon_req(&auth, Some("localhost"))).unwrap(), "");
        // LOCALHOST_BYPASS does NOT help a non-loopback caller: it falls through
        // to the is_configured / first-run checks (only meaningful when the test
        // data dir is genuinely unconfigured).
        if auth.is_configured() {
            assert!(require_user(&anon_req(&auth, Some("10.0.0.5"))).is_err());
        }

        // --- 3. Plain modes (auth on, no bypass): loopback first-run "" vs 401. ---
        std::env::set_var("AUTH_ENABLED", "true");
        std::env::remove_var("LOCALHOST_BYPASS");
        if !auth.is_configured() {
            // Unconfigured first-run: loopback callers get "".
            assert_eq!(require_user(&anon_req(&auth, Some("127.0.0.1"))).unwrap(), "");
            // Non-loopback callers are rejected: HTTPException(401, ...).
            let e = require_user(&anon_req(&auth, Some("10.0.0.5"))).unwrap_err();
            assert!(matches!(e, PyError::Other(ref m) if m == "Not authenticated"));
        } else {
            // Configured: even loopback callers without a resolved user 401.
            assert!(require_user(&anon_req(&auth, Some("127.0.0.1"))).is_err());
        }

        restore("AUTH_ENABLED", &prev_auth);
        restore("LOCALHOST_BYPASS", &prev_bypass);
    }
}
