// src/assistant_log.rs  <- src/assistant_log.py
//! Global utility to post messages to the personal assistant's chat session.
//! Any part of the codebase can call `log_to_assistant()` to surface events,
//! notifications, and results in the assistant's unified activity feed.

use crate::pylog as logger;
use once_cell::sync::Lazy;
use regex::Regex;
use std::sync::Mutex;

// Session manager reference — set by app.py after initialization
//
// Python keeps this as a module-level `None` rebound by `set_session_manager`.
// The session manager is a framework-level object with no faithful Rust shape
// here, so we model the "is it set?" slot with a Mutex<Option<()>>; the actual
// handle would be wired in by the web layer. It is currently unused because
// `log_to_assistant` is a no-op (see below).
static _SESSION_MANAGER: Lazy<Mutex<Option<()>>> = Lazy::new(|| Mutex::new(None));

pub fn set_session_manager(_sm: ()) {
    // global _session_manager
    *_SESSION_MANAGER.lock().unwrap() = Some(_sm);
}

// Pattern callers use to embed a category in the content (legacy):
//   "**[Download]** Started downloading ..."
// We extract that into structured metadata so the UI can color-code by
// category without parsing markdown.
#[allow(dead_code)]
static _LEGACY_TAG_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^\s*\*\*\[([^\]]{1,40})\]\*\*\s*").unwrap());

/// Legacy no-op.
///
/// Older builds wrote system/task activity into a favorited Assistant chat
/// session. Activity now lives in Tasks/notifications, so keep this shim for
/// callers while preventing sidebar-log sessions from being created or filled.
pub fn log_to_assistant(owner: &str, _content: &str, _role: &str, category: Option<&str>) {
    logger::debug(&format!(
        "log_to_assistant ignored legacy activity category={category:?} owner={owner:?}"
    ));
}
