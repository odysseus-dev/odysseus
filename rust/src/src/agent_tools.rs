// src/agent_tools.rs  <- src/agent_tools.py
//! agent_tools.py — Facade module.
//!
//! Re-exports tool parsing, schemas, execution, and implementations for
//! backward compatibility. All importers continue to work unchanged.
//!
//! Sub-modules:
//!   - tool_parsing.py: regex patterns, parse/strip functions
//!   - tool_schemas.py: FUNCTION_TOOL_SCHEMAS, function_call_to_tool_block
//!   - tool_execution.py: execute_tool_block, format_tool_result, MCP helpers
//!   - tool_implementations.py: all do_* tool functions
//!
//! In Rust the "re-export" is just the module tree: callers reach the sub-module
//! items directly (`crate::src::tool_parsing::parse_tool_blocks`,
//! `crate::src::tool_schemas::FUNCTION_TOOL_SCHEMAS`, …). The constants, the
//! `ToolBlock` type, the MCP-manager global, and `_truncate` live HERE (the
//! sub-modules import them from here), exactly as in the Python.

use once_cell::sync::Lazy;
use std::collections::HashSet;
use std::sync::{Arc, Mutex};

use crate::src::mcp_manager::McpManager;

// ---------------------------------------------------------------------------
// Constants (kept here — sub-modules import from here)
// ---------------------------------------------------------------------------
pub const MAX_AGENT_ROUNDS: i64 = 20;
pub const SHELL_TIMEOUT: i64 = 60;
pub const PYTHON_TIMEOUT: i64 = 30;
pub const MAX_OUTPUT_CHARS: i64 = 10_000;
pub const MAX_READ_CHARS: i64 = 20_000;

/// Tool types that trigger execution.
pub static TOOL_TAGS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "bash", "python", "web_search", "web_fetch", "read_file", "write_file",
        "create_document", "update_document", "edit_document",
        "search_chats",
        "chat_with_model", "create_session", "list_sessions",
        "send_to_session",
        "pipeline",
        "manage_session", "manage_memory", "list_models",
        "ui_control", "generate_image",
        "manage_tasks", "api_call", "ask_teacher", "manage_skills",
        "suggest_document",
        "manage_endpoints", "manage_mcp", "manage_webhooks",
        "manage_tokens", "manage_documents", "manage_settings",
        "manage_notes", "manage_calendar",
        "resolve_contact", "manage_contact", "list_email_accounts", "send_email", "list_emails",
        "read_email", "reply_to_email", "bulk_email", "archive_email",
        "delete_email", "mark_email_read",
        // Cookbook tools (LLM serving + downloads). Without these entries,
        // native function calls to e.g. list_served_models are rejected as
        // "Unknown function call" before reaching the dispatcher — silent
        // failure for the whole cookbook surface.
        "download_model", "serve_model",
        "list_served_models", "stop_served_model",
        "list_downloads", "cancel_download",
        "search_hf_models", "list_cached_models",
        "list_serve_presets", "serve_preset", "adopt_served_model",
        "list_cookbook_servers",
        // Other tools the agent reaches for that were also missing.
        "edit_image", "trigger_research", "manage_research",
        // Generic loopback to any UI-button endpoint (cookbook, gallery,
        // email folders, etc.) — agent uses this when there's no named tool
        // wrapper for the action.
        "app_api",
    ]
    .into_iter()
    .collect()
});

/// `ToolBlock = namedtuple("ToolBlock", ["tool_type", "content"])`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToolBlock {
    pub tool_type: String,
    pub content: String,
}

impl ToolBlock {
    /// `ToolBlock(tool_type, content)`
    pub fn new(tool_type: &str, content: &str) -> Self {
        ToolBlock {
            tool_type: tool_type.to_string(),
            content: content.to_string(),
        }
    }
}

// ---------------------------------------------------------------------------
// MCP Manager (kept here — used by execution and agent_loop)
// ---------------------------------------------------------------------------
//
// Python keeps `_mcp_manager = None` as a module global with set/get accessors
// (src/agent_tools.py:68-77). The slot holds the live `McpManager` shared as an
// `Arc` behind a process-global lock; the SAME `Arc` is shared with
// `AppState.mcp_manager`, the HTTP routes, and the startup connect/disconnect.
// `set_mcp_manager` is called from `web/mod.rs` at startup (mirrors
// `app.py:562-563`). `get_mcp_manager` returns a cheap refcount clone, matching
// the Python `get` that returns the same shared object. The import edge is
// one-directional (`agent_tools -> mcp_manager`); `mcp_manager` does not import
// `agent_tools`, so there is no cycle.
static _MCP_MANAGER: Lazy<Mutex<Option<Arc<McpManager>>>> = Lazy::new(|| Mutex::new(None));

/// Set the global MCP manager instance.
pub fn set_mcp_manager(manager: Arc<McpManager>) {
    let mut slot = _MCP_MANAGER.lock().unwrap();
    *slot = Some(manager);
}

/// Get the global MCP manager instance.
pub fn get_mcp_manager() -> Option<Arc<McpManager>> {
    _MCP_MANAGER.lock().unwrap().clone()
}

// ---------------------------------------------------------------------------
// Helpers (kept here — used by sub-modules)
// ---------------------------------------------------------------------------

/// `_truncate(text, limit=MAX_OUTPUT_CHARS)`.
///
/// `len(text)`/`text[:limit]` are over Unicode code points in Python, so this
/// counts and slices by `char`, not by byte.
pub fn _truncate(text: &str, limit: i64) -> String {
    // if len(text) > limit:
    if (text.chars().count() as i64) > limit {
        // return text[:limit] + f"\n... (truncated, {len(text)} chars total)"
        let head: String = text.chars().take(limit as usize).collect();
        let total = text.chars().count();
        format!("{head}\n... (truncated, {total} chars total)")
    } else {
        text.to_string()
    }
}
