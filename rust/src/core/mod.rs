// core/mod.rs  <- core/__init__.py
//! Chat Core — the essential chat experience.
//!
//! This package contains only what's needed for streaming LLM responses,
//! session management, model routing, and authentication.

pub mod atomic_io;
pub mod auth;
pub mod constants;
pub mod exceptions;
pub mod models;

// The database layer (core/database.py, 1776 lines) translated to rusqlite.
// The DB-backed `session_manager` builds on it. Always compiled (no feature flags).
pub mod database;

// The DB-backed session manager (core/session_manager.py) — all session business
// logic + DB operations, built on `database`. Always compiled (no feature flags).
pub mod session_manager;

// The shared middleware (core/middleware.py) — security headers + the
// internal-tool token + `require_admin`. Always compiled (no feature flags).
pub mod middleware;

// The model-endpoint config subset (routes/model_routes.py) the chat interface
// needs: saved endpoints (base URL + encrypted key) + the `/models` probe.
pub mod model_endpoints;

// Connect via the OpenAI Codex subscription: drives the `codex` CLI's
// `app-server` over JSON-RPC (auth delegated to `codex login`). Always compiled.
// This is **Mode A** (the `codex:` URL scheme): Codex runs as a full agent
// SERVER-SIDE with its own tools.
pub mod codex;

// **Mode B** (the `codex-responses:` URL scheme): treat Codex as a plain MODEL
// backend — no `codex` subprocess. Odysseus calls the ChatGPT Responses API
// directly over HTTPS and drives tools with ITS OWN agent loop. Reads/refreshes
// the `codex login` tokens in `CODEX_HOME/auth.json`. Always compiled.
pub mod codex_responses;

// ---- Re-exports mirroring `core/__init__.py`'s public surface ----
//
// NOTE: `core/__init__.py` also re-exports `llm_call`, `stream_llm`, … from
// `src.llm_core`, plus `SecurityHeadersMiddleware` and `SessionManager`; those
// belong to later translation tranches and are not re-exported yet.
pub use auth::AuthManager;
pub use constants::*;
pub use exceptions::{
    InvalidFileUploadError, LLMServiceError, SessionNotFoundError, WebSearchError,
};
pub use models::{ChatMessage, Session};
pub use session_manager::SessionManager;
