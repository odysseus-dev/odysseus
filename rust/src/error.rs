//! Shared error plumbing for the rewrite.
//!
//! Python signals failure by *raising* builtin exceptions (`ValueError`,
//! `PermissionError`, `NotImplementedError`, `KeyError`, `OSError`, …). The
//! faithful Rust translation of "this can raise" is `Result<_, PyError>`, where
//! `PyError` carries the same exception *kind* and message so the behaviour and
//! error strings line up with the Python.
//!
//! Module-specific exception *classes* defined in the Python (e.g.
//! `SessionNotFoundError` in `src/exceptions.py`) are translated in their own
//! modules; this enum only models the builtin exceptions the foundation raises.

use std::fmt;

/// A translated Python builtin exception.
#[derive(Debug)]
pub enum PyError {
    /// Python `ValueError`.
    Value(String),
    /// Python `PermissionError`.
    Permission(String),
    /// Python `NotImplementedError`.
    NotImplemented(String),
    /// Python `KeyError`.
    Key(String),
    /// Python `OSError` / `IOError` (filesystem and other OS failures).
    Os(std::io::Error),
    /// Errors bubbling out of `json.load` / `json.dump` (also a `ValueError`
    /// subclass in CPython via `json.JSONDecodeError`).
    Json(serde_json::Error),
    /// A `fastapi.HTTPException(status_code, detail)` raised on the LLM upstream
    /// path (`src/llm_core.py` — the 503 cooldown / 503 connect-failure / the
    /// real upstream `r.status_code` + friendly detail / the 502 schema /
    /// 502-after-retries raises). Carries BOTH the HTTP `status` and the
    /// human-readable `detail` so the caller (`routes::webhook_routes::sync_chat`)
    /// can propagate the FAITHFUL status without re-deriving it from a message
    /// prefix. `Display` yields ONLY the `detail` (the friendly sentence), so
    /// every other caller that just logs/formats the error keeps printing the
    /// same friendly text it always did.
    Upstream { status: u16, detail: String },
    /// Catch-all for any other raised `Exception`.
    Other(String),
}

/// The translation of "a function that may raise".
pub type PyResult<T> = Result<T, PyError>;

impl fmt::Display for PyError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            PyError::Value(m) => write!(f, "{m}"),
            PyError::Permission(m) => write!(f, "{m}"),
            PyError::NotImplemented(m) => write!(f, "{m}"),
            PyError::Key(m) => write!(f, "{m}"),
            PyError::Os(e) => write!(f, "{e}"),
            PyError::Json(e) => write!(f, "{e}"),
            // Yield ONLY the friendly detail (the upstream HTTPException's `detail`
            // string), so callers that log/format `{e}` print the same friendly
            // sentence the Python `HTTPException(status, friendly)` carries.
            PyError::Upstream { detail, .. } => write!(f, "{detail}"),
            PyError::Other(m) => write!(f, "{m}"),
        }
    }
}

impl std::error::Error for PyError {}

impl From<std::io::Error> for PyError {
    fn from(e: std::io::Error) -> Self {
        PyError::Os(e)
    }
}

impl From<serde_json::Error> for PyError {
    fn from(e: serde_json::Error) -> Self {
        PyError::Json(e)
    }
}

/// Convenience constructors that read like the Python `raise` they replace.
impl PyError {
    pub fn value(msg: impl Into<String>) -> Self {
        PyError::Value(msg.into())
    }
    pub fn permission(msg: impl Into<String>) -> Self {
        PyError::Permission(msg.into())
    }
    pub fn not_implemented(msg: impl Into<String>) -> Self {
        PyError::NotImplemented(msg.into())
    }
    pub fn key(msg: impl Into<String>) -> Self {
        PyError::Key(msg.into())
    }
    pub fn other(msg: impl Into<String>) -> Self {
        PyError::Other(msg.into())
    }
    /// `raise HTTPException(status, detail)` on the LLM upstream path — carries
    /// the faithful status alongside the friendly detail sentence.
    pub fn upstream(status: u16, detail: impl Into<String>) -> Self {
        PyError::Upstream {
            status,
            detail: detail.into(),
        }
    }

    /// The Python `type(exc).__name__` for this error, used where the source
    /// formats an error string as `f"{type(e).__name__}: {e}"`.
    ///
    /// `Other` is the catch-all for raised `Exception`s; the foundation's
    /// generic raises (e.g. `_execute_llm_task`/`_execute_research_task` raising
    /// `RuntimeError("No model/endpoint configured")`) land here, so it maps to
    /// `"RuntimeError"` — the dominant Python type for these paths.
    pub fn py_type_name(&self) -> &'static str {
        match self {
            PyError::Value(_) => "ValueError",
            PyError::Permission(_) => "PermissionError",
            PyError::NotImplemented(_) => "NotImplementedError",
            PyError::Key(_) => "KeyError",
            PyError::Os(_) => "OSError",
            PyError::Json(_) => "JSONDecodeError",
            // A raised `fastapi.HTTPException` — its `type(e).__name__`.
            PyError::Upstream { .. } => "HTTPException",
            PyError::Other(_) => "RuntimeError",
        }
    }
}
