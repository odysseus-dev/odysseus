//! Odysseus — line-by-line-like Rust rewrite.
//!
//! This crate is a parallel translation of the Odysseus Python AI workspace.
//! The Python source remains the source of truth and is left untouched in the
//! repository root; each Rust module mirrors a Python module as closely as the
//! language allows (same names, same control flow, same docstrings as `///`).
//!
//! Module paths mirror Python import paths:
//!   * `core::auth::AuthManager`        <- `core.auth.AuthManager`
//!   * `src::text_helpers::sanitize_text` <- `src.text_helpers.sanitize_text`

// Cosmetic markdown-indent lints on a doc-heavy 1:1 Python port: the `///`
// docstrings mirror the Python docstrings verbatim, so reflowing ~153 list
// items (90 over-indented + 63 lazy-continuation) would only diverge the
// Rust docs from the Python source of truth for zero behavioral gain.
#![allow(clippy::doc_overindented_list_items)]
#![allow(clippy::doc_lazy_continuation)]

pub mod error;

// Support shims: the slices of the Python standard library (and a couple of
// third-party libs) the translation leans on, so modules can read line-by-line
// like the original.
pub mod pybuiltins;
pub mod pydatetime;
pub mod pylog;
pub mod pyos;
pub mod pyotp;
pub mod pysecrets;
pub mod pytime;

// Mirrors the Python `core/` package.
pub mod core;

// Mirrors the Python `src/` package. (A Rust module literally named `src`,
// living at `rust/src/src/`, so the import path matches `src.<module>`.)
pub mod src;

// Mirrors the Python `routes/` package (pure-logic helpers plus the translated
// FastAPI routers — all compiled in the single build).
pub mod routes;

// Mirrors the Python `services/` package — plug-in capabilities for the chat
// core (search/docs/research/memory/shell/tts/stt/hwfit/youtube). The
// every part (memory skill scanning, hwfit hardware-fit math, youtube re-export,
// plus the async/HTTP/LLM/DB surfaces) is always compiled — the crate has no
// cargo feature flags (see `services/mod.rs`).
pub mod services;

// The runnable server — the axum translation of `app.py`.
// This is the binary's entry point (`src/main.rs` calls `web::run()`).
pub mod web;

// Mirrors the Python `mcp_servers/` package — the built-in MCP stdio servers
// (email/memory/image_gen/rag + the shared `_common`). They speak the same
// newline-delimited JSON-RPC the `src::mcp_manager` client drives, alongside the
// async stdio + DB/IMAP machinery they reuse.
pub mod mcp_servers;
