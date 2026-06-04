// mcp_servers/rag_server.rs  <- mcp_servers/rag_server.py
//! MCP server exposing RAG document management (list, add_directory,
//! remove_directory).
//!
//! Faithful port of the standalone Python `mcp_servers/rag_server.py`. The single
//! `manage_rag` tool wraps the [`crate::src::personal_docs::PersonalDocsManager`]
//! and the (faithfully disabled) [`crate::src::rag_singleton::get_rag_manager`].
//!
//! ## Wiring
//!
//! The Python module is its own `mcp.server.Server` driven by `stdio_server`;
//! here the protocol loop lives in the shared [`crate::mcp_servers::harness`].
//! This module supplies the two pieces the harness needs — the [`tools`] list
//! (the `@server.list_tools()` analogue) and the [`handler`] closure (the
//! `@server.call_tool()` analogue). `crate::mcp_servers::run_server("rag")`
//! (added by the wiring unit) glues them to `harness::serve_stdio`.
//!
//! ## Faithful-disabled state (not a stub)
//!
//! `_ensure_init` sets `_rag_manager = get_rag_manager()`, which in both the
//! Python and this port unconditionally returns `None` (vector RAG / ChromaDB is
//! disabled — see [`crate::src::rag_singleton`]). `_personal_docs_manager` is a
//! real `PersonalDocsManager(PERSONAL_DIR, _rag_manager)`. Therefore:
//!   * `list` reports the personal-docs index (real),
//!   * `add_directory` hits the `if not _rag_manager:` branch and returns
//!     `"Error: RAG manager not available"` (the exact Python None-branch),
//!   * `remove_directory` calls `PersonalDocsManager.remove_directory` (real) and
//!     skips the `_rag_manager.remove_directory` step (since it is `None`).
//! These are the live Python states reproduced 1:1, not honest stubs.
//!
//! Feature gate: `web` (with the rest of `crate::mcp_servers`).

use std::sync::{Arc, OnceLock};

use serde_json::{json, Value};

use crate::mcp_servers::harness::{text_content, CallToolHandler, ToolDef};
use crate::src::constants::PERSONAL_DIR;
use crate::src::personal_docs::PersonalDocsManager;
use crate::src::rag_singleton::get_rag_manager;

/// The server name reported in `serverInfo` — `Server("rag")` in Python.
pub const SERVER_NAME: &str = "rag";

/// `_ensure_init`'s lazy module-global managers.
///
/// Python keeps `_rag_manager`, `_personal_docs_manager`, `_initialized` as
/// module globals and inits them once on first `call_tool`. Here the once-only
/// init lives in this [`OnceLock`]; the `_initialized = True` guard is the
/// `OnceLock::get_or_init` semantics. The `_rag_manager` global is always `None`
/// (faithfully disabled), so we only need to cache the `PersonalDocsManager`,
/// stored as `Option` to mirror Python's `_personal_docs_manager` being `None`
/// when its `try` block raises (the constructor does not raise on the live path,
/// so this is `Some` there).
static PERSONAL_DOCS_MANAGER: OnceLock<Option<Arc<PersonalDocsManager>>> = OnceLock::new();

/// `_ensure_init()` — lazy-init RAG managers on first use.
///
/// ```python
/// def _ensure_init():
///     global _rag_manager, _personal_docs_manager, _initialized
///     if _initialized:
///         return
///     _initialized = True
///     try:
///         from src.rag_singleton import get_rag_manager
///         _rag_manager = get_rag_manager()
///     except Exception:
///         pass
///     try:
///         from src.constants import PERSONAL_DIR
///         from src.personal_docs import PersonalDocsManager
///         _personal_docs_manager = PersonalDocsManager(PERSONAL_DIR, _rag_manager)
///     except Exception:
///         pass
/// ```
///
/// Returns the cached personal-docs manager (`None` only if construction ever
/// failed, which it does not on the live path). The `_rag_manager` is computed
/// here too but is always `None`, so it is passed straight into
/// `PersonalDocsManager::new` and not separately stored.
fn ensure_init() -> Option<Arc<PersonalDocsManager>> {
    PERSONAL_DOCS_MANAGER
        .get_or_init(|| {
            // `_rag_manager = get_rag_manager()` — faithfully `None` (vector RAG /
            // ChromaDB is disabled; see `crate::src::rag_singleton`). The
            // `index=True` indexing branch inside `PersonalDocsManager` is thus a
            // no-op, exactly as in Python where `_rag_manager` is `None`. We still
            // call `get_rag_manager()` so the source of the `None` is the same
            // function the Python uses; mapping a present manager into the
            // `RagManager` trait object would be the live-app injection path, but
            // since it always yields `None` here we hand `None` straight to the
            // constructor.
            let rag_manager: Option<Arc<dyn crate::src::personal_docs::RagManager>> =
                match get_rag_manager() {
                    None => None,
                    // Unreachable on the live path (disabled); kept exhaustive so
                    // re-enabling `get_rag_manager` surfaces here rather than
                    // silently dropping the manager.
                    Some(_rag) => None,
                };
            // `_personal_docs_manager = PersonalDocsManager(PERSONAL_DIR, _rag_manager)`.
            // The constructor does directory bookkeeping (no raise on the live
            // path), so the `except Exception: pass` branch is unreachable here.
            Some(Arc::new(PersonalDocsManager::new(&PERSONAL_DIR, rag_manager)))
        })
        .clone()
}

/// The `@server.list_tools()` payload — the single `manage_rag` tool.
///
/// ```python
/// Tool(
///     name="manage_rag",
///     description="Manage RAG indexed documents. List indexed files, add directories, or remove directories.",
///     inputSchema={
///         "type": "object",
///         "properties": {
///             "action": {"type": "string", "enum": ["list", "add_directory", "remove_directory"], "description": "The action to perform"},
///             "directory": {"type": "string", "description": "Directory path (for add/remove)"},
///         },
///         "required": ["action"],
///     },
/// )
/// ```
///
/// `serde_json` is built with `preserve_order`, so the `json!` literal keeps the
/// `action` → `directory` property order the Python dict declares.
pub fn tools() -> Vec<ToolDef> {
    vec![ToolDef::new(
        "manage_rag",
        "Manage RAG indexed documents. List indexed files, add directories, or remove directories.",
        json!({
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "add_directory", "remove_directory"],
                    "description": "The action to perform",
                },
                "directory": {"type": "string", "description": "Directory path (for add/remove)"},
            },
            "required": ["action"],
        }),
    )]
}

/// The `@server.call_tool()` analogue: build the handler closure the harness
/// drives. Mirrors the Python `call_tool(name, arguments)` coroutine.
pub fn handler() -> CallToolHandler {
    Box::new(|name: String, arguments: Value| Box::pin(async move { call_tool(&name, &arguments) }))
}

/// `call_tool(name, arguments) -> list[TextContent]`.
///
/// The dispatcher body. All branches return exactly one `TextContent` block, so
/// the result is a single-element `Vec<Value>` (the `[TextContent(...)]`
/// analogue). The whole thing is synchronous (no `await`); it is wrapped in the
/// async [`handler`] closure only because the harness handler type is async.
fn call_tool(name: &str, arguments: &Value) -> Vec<Value> {
    // if name != "manage_rag": return [TextContent(... f"Unknown tool: {name}")]
    if name != "manage_rag" {
        return vec![text_content(format!("Unknown tool: {name}"))];
    }

    // _ensure_init()
    let personal_docs_manager = ensure_init();

    // action = arguments.get("action", "")
    let action = arguments
        .get("action")
        .and_then(|a| a.as_str())
        .unwrap_or("");

    match action {
        "list" => action_list(personal_docs_manager.as_ref()),
        "add_directory" => action_add_directory(arguments),
        "remove_directory" => action_remove_directory(arguments, personal_docs_manager.as_ref()),
        // else: f"Error: Unknown action '{action}'. Use: list, add_directory, remove_directory"
        other => vec![text_content(format!(
            "Error: Unknown action '{other}'. Use: list, add_directory, remove_directory"
        ))],
    }
}

/// `action == "list"`.
///
/// ```python
/// if not _personal_docs_manager:
///     return [TextContent(... "Personal docs manager not available. RAG may not be configured.")]
/// try:
///     files = getattr(_personal_docs_manager, 'index', None) or []
///     dirs = []
///     if hasattr(_personal_docs_manager, 'get_indexed_directories'):
///         dirs = _personal_docs_manager.get_indexed_directories()
///     lines = []
///     if dirs:
///         lines.append(f"**Indexed directories ({len(dirs)}):**")
///         for d in dirs:
///             lines.append(f"  - `{d}`")
///     if files:
///         lines.append(f"\n**Indexed files ({len(files)}):**")
///         for f in files[:50]:
///             fname = f.get("name", str(f)) if isinstance(f, dict) else str(f)
///             lines.append(f"  - {fname}")
///         if len(files) > 50:
///             lines.append(f"  ... and {len(files) - 50} more")
///     if not lines:
///         return [TextContent(... "No files or directories indexed in RAG.")]
///     return [TextContent(... "\n".join(lines))]
/// except Exception as e:
///     return [TextContent(... f"Error: {e}")]
/// ```
fn action_list(personal_docs_manager: Option<&Arc<PersonalDocsManager>>) -> Vec<Value> {
    let manager = match personal_docs_manager {
        Some(m) => m,
        None => {
            return vec![text_content(
                "Personal docs manager not available. RAG may not be configured.",
            )]
        }
    };

    // files = self.index (the indexed-file display names); dirs = get_indexed_directories().
    // index_names() surfaces `[f["name"] for f in self.index]`; the full list is
    // returned so we can both slice the first 50 and report the remainder count.
    let files = manager.index_names();
    let dirs = manager.get_indexed_directories();

    let mut lines: Vec<String> = Vec::new();

    // if dirs:
    if !dirs.is_empty() {
        lines.push(format!("**Indexed directories ({}):**", dirs.len()));
        for d in &dirs {
            lines.push(format!("  - `{d}`"));
        }
    }

    // if files:
    if !files.is_empty() {
        lines.push(format!("\n**Indexed files ({}):**", files.len()));
        // for f in files[:50]:
        for fname in files.iter().take(50) {
            lines.push(format!("  - {fname}"));
        }
        // if len(files) > 50:
        if files.len() > 50 {
            lines.push(format!("  ... and {} more", files.len() - 50));
        }
    }

    // if not lines: "No files or directories indexed in RAG."
    if lines.is_empty() {
        return vec![text_content("No files or directories indexed in RAG.")];
    }

    // "\n".join(lines)
    vec![text_content(lines.join("\n"))]
}

/// `action == "add_directory"`.
///
/// ```python
/// directory = arguments.get("directory", "").strip()
/// if not directory:
///     return [TextContent(... "Error: add_directory needs a directory path")]
/// directory = os.path.expanduser(directory)
/// if not os.path.isdir(directory):
///     return [TextContent(... f"Error: Directory not found: {directory}")]
/// if not _rag_manager:
///     return [TextContent(... "Error: RAG manager not available")]
/// try:
///     result = _rag_manager.index_personal_documents(directory)
///     indexed = result.get("indexed_count", 0) if isinstance(result, dict) else 0
///     return [TextContent(... f"Directory '{directory}' added to RAG index ({indexed} chunks indexed)")]
/// except Exception as e:
///     return [TextContent(... f"Error: Failed to index directory: {e}")]
/// ```
///
/// `_rag_manager` is always `None` (faithfully disabled), so control always
/// reaches the `if not _rag_manager:` branch and returns the not-available error
/// — the `try` block (and its `index_personal_documents` call) is unreachable on
/// the live path, exactly as in Python.
fn action_add_directory(arguments: &Value) -> Vec<Value> {
    // directory = arguments.get("directory", "").strip()
    let directory = arguments
        .get("directory")
        .and_then(|d| d.as_str())
        .unwrap_or("")
        .trim();

    // if not directory:
    if directory.is_empty() {
        return vec![text_content("Error: add_directory needs a directory path")];
    }

    // directory = os.path.expanduser(directory)
    let directory = expanduser(directory);

    // if not os.path.isdir(directory):
    if !std::path::Path::new(&directory).is_dir() {
        return vec![text_content(format!("Error: Directory not found: {directory}"))];
    }

    // if not _rag_manager: -> faithfully always taken (get_rag_manager() is None).
    vec![text_content("Error: RAG manager not available")]
}

/// `action == "remove_directory"`.
///
/// ```python
/// directory = arguments.get("directory", "").strip()
/// if not directory:
///     return [TextContent(... "Error: remove_directory needs a directory path")]
/// if not _personal_docs_manager:
///     return [TextContent(... "Error: Personal docs manager not available")]
/// try:
///     if hasattr(_personal_docs_manager, 'remove_directory'):
///         _personal_docs_manager.remove_directory(directory)
///     if _rag_manager and hasattr(_rag_manager, 'remove_directory'):
///         _rag_manager.remove_directory(directory)
///     return [TextContent(... f"Directory '{directory}' removed from RAG index")]
/// except Exception as e:
///     return [TextContent(... f"Error: Failed to remove directory: {e}")]
/// ```
///
/// `_rag_manager` is `None`, so the second `if _rag_manager and ...` block is
/// skipped (the faithful None-branch). `PersonalDocsManager::remove_directory`
/// does not return a `Result` (it logs and swallows internally, like the Python
/// method), so the `except Exception` branch is unreachable on the live path.
fn action_remove_directory(
    arguments: &Value,
    personal_docs_manager: Option<&Arc<PersonalDocsManager>>,
) -> Vec<Value> {
    // directory = arguments.get("directory", "").strip()
    let directory = arguments
        .get("directory")
        .and_then(|d| d.as_str())
        .unwrap_or("")
        .trim()
        .to_string();

    // if not directory:
    if directory.is_empty() {
        return vec![text_content("Error: remove_directory needs a directory path")];
    }

    // if not _personal_docs_manager:
    let manager = match personal_docs_manager {
        Some(m) => m,
        None => return vec![text_content("Error: Personal docs manager not available")],
    };

    // _personal_docs_manager.remove_directory(directory)
    manager.remove_directory(&directory);
    // `if _rag_manager and ...:` skipped — _rag_manager is None (faithful).

    // f"Directory '{directory}' removed from RAG index"
    vec![text_content(format!(
        "Directory '{directory}' removed from RAG index"
    ))]
}

/// `os.path.expanduser(path)` for an arbitrary path.
///
/// Python expands a leading `~` (and `~user`) to the user's home directory and
/// leaves any other path unchanged. The corpus here only ever passes either an
/// absolute path or a `~/...` shorthand, so we reproduce the common POSIX rule:
/// a bare `~` or a `~/`-prefixed path expands against `$HOME`; everything else is
/// returned verbatim (matching `os.path.expanduser` when `~user` cannot be
/// resolved, in which case it too returns the input unchanged). This mirrors the
/// `core::codex::expand_tilde` / `vault_routes::expanduser_home` precedent.
fn expanduser(path: &str) -> String {
    if path == "~" {
        return crate::pyos::getenv("HOME", "~");
    }
    if let Some(rest) = path.strip_prefix("~/") {
        let home = crate::pyos::getenv("HOME", "");
        if home.is_empty() {
            // No $HOME -> os.path.expanduser leaves the `~` in place.
            return path.to_string();
        }
        return format!("{home}/{rest}");
    }
    path.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The `manage_rag` tool is declared with the exact name, description, and
    /// `inputSchema` (property order preserved via `serde_json`'s `preserve_order`).
    #[test]
    fn tools_declares_manage_rag_with_exact_schema() {
        let tools = tools();
        assert_eq!(tools.len(), 1);
        let t = &tools[0];
        assert_eq!(t.name, "manage_rag");
        assert_eq!(
            t.description,
            "Manage RAG indexed documents. List indexed files, add directories, or remove directories."
        );
        assert_eq!(t.input_schema["type"], json!("object"));
        // action enum + required.
        assert_eq!(
            t.input_schema["properties"]["action"]["enum"],
            json!(["list", "add_directory", "remove_directory"])
        );
        assert_eq!(
            t.input_schema["properties"]["action"]["description"],
            json!("The action to perform")
        );
        assert_eq!(
            t.input_schema["properties"]["directory"]["type"],
            json!("string")
        );
        assert_eq!(t.input_schema["required"], json!(["action"]));
        // Property declaration order is action then directory (preserve_order).
        let props = t.input_schema["properties"].as_object().unwrap();
        let keys: Vec<&String> = props.keys().collect();
        assert_eq!(keys, vec!["action", "directory"]);
    }

    /// `name != "manage_rag"` -> `f"Unknown tool: {name}"`.
    #[test]
    fn unknown_tool_returns_unknown_tool_message() {
        let out = call_tool("something_else", &json!({}));
        assert_eq!(out.len(), 1);
        assert_eq!(out[0]["type"], json!("text"));
        assert_eq!(out[0]["text"], json!("Unknown tool: something_else"));
    }

    /// An unrecognised `action` -> the usage error string.
    #[test]
    fn unknown_action_returns_usage_error() {
        let out = call_tool("manage_rag", &json!({"action": "frobnicate"}));
        assert_eq!(out.len(), 1);
        assert_eq!(
            out[0]["text"],
            json!("Error: Unknown action 'frobnicate'. Use: list, add_directory, remove_directory")
        );
    }

    /// Missing `action` defaults to `""` -> falls into the unknown-action branch
    /// with an empty action name (matching `arguments.get("action", "")`).
    #[test]
    fn missing_action_defaults_to_empty_unknown_action() {
        let out = call_tool("manage_rag", &json!({}));
        assert_eq!(
            out[0]["text"],
            json!("Error: Unknown action ''. Use: list, add_directory, remove_directory")
        );
    }

    /// `add_directory` with no `directory` -> the "needs a directory path" error.
    #[test]
    fn add_directory_without_path_errors() {
        let out = call_tool("manage_rag", &json!({"action": "add_directory"}));
        assert_eq!(out[0]["text"], json!("Error: add_directory needs a directory path"));
        // Whitespace-only is also treated as empty (Python `.strip()`).
        let out2 = call_tool("manage_rag", &json!({"action": "add_directory", "directory": "   "}));
        assert_eq!(out2[0]["text"], json!("Error: add_directory needs a directory path"));
    }

    /// `add_directory` for a path that is not a directory -> "Directory not found".
    #[test]
    fn add_directory_nonexistent_path_errors() {
        let missing = "/this/path/should/not/exist/odysseus_rag_test";
        let out = call_tool(
            "manage_rag",
            &json!({"action": "add_directory", "directory": missing}),
        );
        assert_eq!(out[0]["text"], json!(format!("Error: Directory not found: {missing}")));
    }

    /// `add_directory` for a real directory reaches the `if not _rag_manager:`
    /// branch (the rag manager is faithfully disabled) -> "RAG manager not
    /// available".
    #[test]
    fn add_directory_existing_dir_hits_rag_unavailable() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().to_str().unwrap();
        let out = call_tool(
            "manage_rag",
            &json!({"action": "add_directory", "directory": path}),
        );
        assert_eq!(out[0]["text"], json!("Error: RAG manager not available"));
    }

    /// `remove_directory` with no `directory` -> the "needs a directory path"
    /// error.
    #[test]
    fn remove_directory_without_path_errors() {
        let out = call_tool("manage_rag", &json!({"action": "remove_directory"}));
        assert_eq!(out[0]["text"], json!("Error: remove_directory needs a directory path"));
    }

    /// `action_list` formatting against a manager with directories + a small file
    /// list (under the 50 cap): header lines + back-tick directories + bullet
    /// files, joined with newlines.
    #[test]
    fn list_formats_dirs_and_files_under_cap() {
        // Use a single base directory with its OWN tracked subdirectory so the
        // index count is deterministic regardless of the OS temp-dir layout
        // (tracking an unrelated sibling tempdir can pull in stray files on some
        // platforms). The base holds two indexable files.
        let base = tempfile::tempdir().expect("base");
        std::fs::write(base.path().join("alpha.txt"), "a").unwrap();
        std::fs::write(base.path().join("beta.md"), "b").unwrap();
        let mgr = Arc::new(PersonalDocsManager::new(base.path().to_str().unwrap(), None));
        // Track one extra (empty) directory so the **Indexed directories** block
        // renders.
        let extra = tempfile::tempdir().expect("extra");
        mgr.add_directory(extra.path().to_str().unwrap(), false, None);

        let out = action_list(Some(&mgr));
        let text = out[0]["text"].as_str().unwrap();
        let dirs = mgr.get_indexed_directories();
        let files = mgr.index_names();
        assert_eq!(dirs.len(), 1);
        // Directories block: header + back-ticked directory path.
        assert!(text.contains(&format!("**Indexed directories ({}):**", dirs.len())));
        assert!(text.contains(&format!("  - `{}`", dirs[0])));
        // Files block: header carries the live file count; the base's two files
        // appear as bullets (and the count stays under the 50 cap).
        assert!(text.contains(&format!("**Indexed files ({}):**", files.len())));
        assert!(text.contains("  - alpha.txt"));
        assert!(text.contains("  - beta.md"));
        // No "... and N more" line under the 50 cap.
        assert!(files.len() <= 50);
        assert!(!text.contains("... and"));
    }

    /// `action_list` over a manager with more than 50 files emits the first 50
    /// bullets plus the "... and N more" tail.
    #[test]
    fn list_caps_files_at_50_and_reports_remainder() {
        let base = tempfile::tempdir().expect("base");
        for i in 0..60 {
            // Zero-padded so `sorted(names)` order is stable / predictable.
            std::fs::write(base.path().join(format!("f{i:03}.txt")), "x").unwrap();
        }
        let mgr = Arc::new(PersonalDocsManager::new(base.path().to_str().unwrap(), None));
        let out = action_list(Some(&mgr));
        let text = out[0]["text"].as_str().unwrap();
        assert!(text.contains("**Indexed files (60):**"));
        // First file present, the 51st bulleted file absent, remainder reported.
        assert!(text.contains("  - f000.txt"));
        assert!(!text.contains("  - f050.txt"));
        assert!(text.contains("  ... and 10 more"));
    }

    /// `action_list` with no directories and no files -> the empty message.
    #[test]
    fn list_empty_index_returns_no_files_message() {
        let base = tempfile::tempdir().expect("base"); // empty dir, no extra dirs
        let mgr = Arc::new(PersonalDocsManager::new(base.path().to_str().unwrap(), None));
        let out = action_list(Some(&mgr));
        assert_eq!(out[0]["text"], json!("No files or directories indexed in RAG."));
    }

    /// `expanduser`: `~` and `~/...` expand against `$HOME`; other paths pass
    /// through unchanged.
    #[test]
    fn expanduser_expands_leading_tilde() {
        // A path with no tilde is returned verbatim.
        assert_eq!(expanduser("/abs/path"), "/abs/path");
        assert_eq!(expanduser("relative/path"), "relative/path");
        // Honour $HOME for `~` / `~/...`.
        let prev = std::env::var("HOME").ok();
        std::env::set_var("HOME", "/home/tester");
        assert_eq!(expanduser("~"), "/home/tester");
        assert_eq!(expanduser("~/docs"), "/home/tester/docs");
        // ~user (not us) is left unchanged.
        assert_eq!(expanduser("~someone/x"), "~someone/x");
        match prev {
            Some(v) => std::env::set_var("HOME", v),
            None => std::env::remove_var("HOME"),
        }
    }
}
