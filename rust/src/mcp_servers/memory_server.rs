// mcp_servers/memory_server.rs  <- mcp_servers/memory_server.py
//! MCP server exposing memory management (list, add, edit, delete, search).
//!
//! Faithful port of `mcp_servers/memory_server.py`. The Python file builds a
//! single `mcp.server.Server("memory")` with one decorated `list_tools` and one
//! decorated `call_tool` coroutine, then runs it over `stdio_server`. Here the
//! stdio loop lives in [`crate::mcp_servers::harness`]; this module supplies the
//! [`ToolDef`] list (`list_tools` analogue) and the `call_tool` handler closure.
//!
//! It reuses [`crate::src::memory::MemoryManager`] (`load`/`load_all`/`save`/
//! `add_entry`/`get_relevant_memories`) and [`crate::src::memory_vector::
//! MemoryVectorStore`] exactly as the Python imports `src.memory.MemoryManager`
//! and `src.memory_vector.MemoryVectorStore`.
//!
//! ## Faithful state on the live app path
//!
//! The Python `_ensure_init` builds `MemoryVectorStore(DATA_DIR)` and drops it to
//! `None` when `not _memory_vector.healthy`. The Rust ctor is
//! `MemoryVectorStore::new(data_dir, None).await`, and on the live app path no
//! embedding client is injected, so the store reports `healthy() == false` — we
//! therefore set the vector store to `None`, the same as the Python. With the
//! store gone, the `add`/`edit`/`delete` vector touch-ups are no-ops, which is
//! exactly the Python behaviour (every vector op there is wrapped in
//! `try/except: pass`, and the `if _memory_vector and _memory_vector.healthy`
//! guard short-circuits when the store is `None`).
//!
//! Feature gate: `web` (the harness needs tokio async stdio, and the vector store
//! ctor is `web`-gated alongside the embeddings/vector backend).

use std::sync::Arc;

use serde_json::{json, Value};
use tokio::sync::OnceCell;

use crate::mcp_servers::harness::{text_content, CallToolHandler, ToolDef};
use crate::pytime as time;
use crate::src::constants;
use crate::src::memory::MemoryManager;
use crate::src::memory_vector::MemoryVectorStore;

/// `@server.list_tools()` — the single `manage_memory` tool definition.
///
/// Mirrors the Python `list_tools()` coroutine: one `Tool` whose `inputSchema`
/// is the object below (the `action` enum plus `text`/`memory_id`/`category`).
/// The property insertion order matches the Python dict literal exactly.
pub fn tools() -> Vec<ToolDef> {
    vec![ToolDef::new(
        "manage_memory",
        "Manage the user's memory system: list, add, edit, delete, or search memories.",
        json!({
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "add", "edit", "delete", "search"],
                    "description": "The action to perform",
                },
                "text": {"type": "string", "description": "Memory text (add/edit) or search query (search)"},
                "memory_id": {"type": "string", "description": "Memory ID (edit/delete)"},
                "category": {
                    "type": "string",
                    "enum": ["fact", "event", "contact", "preference"],
                    "description": "Memory category (add/list filter)",
                },
            },
            "required": ["action"],
        }),
    )]
}

/// The late-initialized managers (the Python module-level `_memory_manager` /
/// `_memory_vector` / `_initialized` globals).
///
/// The Python lazily initializes these on the first tool call via `_ensure_init`;
/// here the harness handler closure builds the `State` lazily on first
/// `tools/call` via a [`OnceCell`] (the `_initialized` guard analogue), so the
/// "init on first use" timing is preserved exactly.
struct State {
    /// `_memory_manager` — `None` when the manager could not be constructed
    /// (the Python `MemoryManager(DATA_DIR)` would itself raise; here `new`
    /// returns a `PyResult`, and a failure leaves this `None` so the handler
    /// returns "Error: Memory manager not available").
    memory_manager: Option<MemoryManager>,
    /// `_memory_vector` — `None` when the vector store is unhealthy (the live
    /// app path, since no embedding client is injected) or failed to build.
    memory_vector: Option<MemoryVectorStore>,
}

impl State {
    /// `_ensure_init()` — build the memory manager + (optionally) the vector
    /// store. Never raises: a manager build failure leaves `memory_manager`
    /// `None`, and an unhealthy/failed vector store leaves `memory_vector` `None`
    /// (the Python `try/except` around the `MemoryVectorStore` import + the
    /// `if not healthy` drop).
    async fn ensure_init() -> State {
        // _memory_manager = MemoryManager(DATA_DIR)
        // A build failure (the Python `MemoryManager(DATA_DIR)` would raise) leaves
        // this `None` -> the handler returns "Error: Memory manager not available".
        let memory_manager = MemoryManager::new(&constants::DATA_DIR).ok();

        // try:
        //     _memory_vector = MemoryVectorStore(DATA_DIR)
        //     if not _memory_vector.healthy: _memory_vector = None
        // except Exception:
        //     _memory_vector = None
        let store = MemoryVectorStore::new(&constants::DATA_DIR, None).await;
        let memory_vector = if store.healthy() { Some(store) } else { None };

        State { memory_manager, memory_vector }
    }
}

/// Build the `call_tool` handler closure — the `@server.call_tool()` analogue.
///
/// The state (`MemoryManager` + optional `MemoryVectorStore`) is built lazily on
/// the first `tools/call` via a per-closure [`OnceCell`], so the
/// `_ensure_init`-on-first-use timing is preserved (the closure captures the cell
/// and reuses the same `State` for every subsequent call, the analogue of the
/// `_initialized` flag). Each spawned server process drives exactly one session.
pub fn handler() -> CallToolHandler {
    let cell: Arc<OnceCell<State>> = Arc::new(OnceCell::new());
    Box::new(move |name: String, arguments: Value| {
        let cell = cell.clone();
        Box::pin(async move { call_tool(&cell, &name, &arguments).await })
    })
}

/// `call_tool(name, arguments) -> list[TextContent]`.
///
/// Faithful translation of the Python `call_tool` body, branch for branch.
///
/// `_ensure_init()` runs lazily **inside** this function, only after the
/// `name != "manage_memory"` check passes — exactly as the Python orders it
/// (the unknown-tool branch returns before `_ensure_init()` is reached, so an
/// unknown tool name never triggers manager/vector-store init). The
/// [`OnceCell`] is the `_initialized` guard: the `State` is built once on the
/// first known-tool call and reused thereafter.
async fn call_tool(cell: &OnceCell<State>, name: &str, arguments: &Value) -> Vec<Value> {
    // if name != "manage_memory":
    if name != "manage_memory" {
        return vec![text_content(format!("Unknown tool: {name}"))];
    }

    // _ensure_init() — build on first use, reuse thereafter. Reached only after
    // the name check above, so an unknown tool name does not init.
    let state = cell.get_or_init(State::ensure_init).await;

    // if not _memory_manager: ...
    let mgr = match &state.memory_manager {
        Some(mgr) => mgr,
        None => return vec![text_content("Error: Memory manager not available")],
    };

    // action = arguments.get("action", "")
    let action = arg_str(arguments, "action");

    match action.as_str() {
        "list" => list_action(mgr, arguments),
        "add" => add_action(state, mgr, arguments).await,
        "edit" => edit_action(state, mgr, arguments).await,
        "delete" => delete_action(state, mgr, arguments),
        "search" => search_action(mgr, arguments),
        // else: Unknown action
        _ => vec![text_content(format!(
            "Error: Unknown action '{action}'. Use: list, add, edit, delete, search"
        ))],
    }
}

/// `if action == "list":`
fn list_action(mgr: &MemoryManager, arguments: &Value) -> Vec<Value> {
    // category_filter = arguments.get("category", "")
    let category_filter = arg_str(arguments, "category");
    // memories = _memory_manager.load()  (owner-unfiltered)
    let mut memories = mgr.load(None);
    // if category_filter: filter on category.lower() == category_filter.lower()
    if !category_filter.is_empty() {
        let want = category_filter.to_lowercase();
        memories.retain(|m| {
            m.get("category").and_then(|v| v.as_str()).unwrap_or("").to_lowercase() == want
        });
    }
    // if not memories:
    if memories.is_empty() {
        let mut msg = String::from("No memories found");
        if !category_filter.is_empty() {
            msg.push_str(&format!(" in category '{category_filter}'"));
        }
        msg.push('.');
        return vec![text_content(msg)];
    }
    // lines = [f"Found {len(memories)} memory entries:\n"]
    let mut lines: Vec<String> = vec![format!("Found {} memory entries:\n", memories.len())];
    // for m in memories[:100]:
    for m in memories.iter().take(100) {
        let cat = get_str_or(m, "category", "fact");
        // mid = m.get("id", "?")[:8]
        let mid = char_prefix(&get_str_or(m, "id", "?"), 8);
        // text = m.get("text", "")
        let mut text = get_str_or(m, "text", "");
        // if len(text) > 150: text = text[:150] + "..."
        if text.chars().count() > 150 {
            text = format!("{}...", char_prefix(&text, 150));
        }
        lines.push(format!("- [{cat}] `{mid}` — {text}"));
    }
    // if len(memories) > 100:
    if memories.len() > 100 {
        lines.push(format!("... and {} more", memories.len() - 100));
    }
    vec![text_content(lines.join("\n"))]
}

/// `elif action == "add":`
async fn add_action(state: &State, mgr: &MemoryManager, arguments: &Value) -> Vec<Value> {
    // text = arguments.get("text", "")
    let text = arg_str(arguments, "text");
    // category = arguments.get("category", "fact")
    let category = arg_str_default(arguments, "category", "fact");
    // if not text:
    if text.is_empty() {
        return vec![text_content("Error: Memory text cannot be empty")];
    }
    // entry = _memory_manager.add_entry(text, source="ai_agent", category=category)
    // add_entry raises ValueError only on empty text, already guarded above, so
    // this never errors on the reachable path; an unexpected error would
    // propagate out of the Python call_tool (SDK error-wraps it). The harness
    // handler can only return content, so surface the message as text — the
    // least-surprising graceful degradation for an unreachable branch.
    let entry = match mgr.add_entry(&text, "ai_agent", &category, None) {
        Ok(e) => e,
        Err(e) => return vec![text_content(format!("Error: {e}"))],
    };
    // memories = _memory_manager.load_all(); memories.append(entry); save(memories)
    let mut memories = mgr.load_all();
    memories.push(entry.clone());
    if let Err(e) = mgr.save(&mut memories) {
        return vec![text_content(format!("Error: {e}"))];
    }
    // if _memory_vector and _memory_vector.healthy: try: _memory_vector.add(id, text) except: pass
    if let Some(vec_store) = &state.memory_vector {
        if vec_store.healthy() {
            let id = get_str_or(&entry, "id", "");
            // try/except: pass — swallow any error from the vector add.
            let _ = vec_store.add(&id, &text).await;
        }
    }
    // return f"Memory added: [{category}] {text} (id: {entry['id'][:8]})"
    let id8 = char_prefix(&get_str_or(&entry, "id", ""), 8);
    vec![text_content(format!("Memory added: [{category}] {text} (id: {id8})"))]
}

/// `elif action == "edit":`
async fn edit_action(state: &State, mgr: &MemoryManager, arguments: &Value) -> Vec<Value> {
    // memory_id = arguments.get("memory_id", ""); new_text = arguments.get("text", "")
    let memory_id = arg_str(arguments, "memory_id");
    let new_text = arg_str(arguments, "text");
    // if not memory_id or not new_text:
    if memory_id.is_empty() || new_text.is_empty() {
        return vec![text_content("Error: edit needs memory_id and text")];
    }
    // memories = _memory_manager.load_all()
    let mut memories = mgr.load_all();
    let mut found = false;
    let mut full_id: Option<String> = None;
    // for m in memories: if m.get("id", "").startswith(memory_id): ... break
    for m in memories.iter_mut() {
        let id = get_str_or(m, "id", "");
        if id.starts_with(&memory_id) {
            if let Some(obj) = m.as_object_mut() {
                obj.insert("text".to_string(), json!(new_text));
                // m["timestamp"] = int(time.time())
                obj.insert("timestamp".to_string(), json!(time::time() as i64));
            }
            found = true;
            full_id = Some(id);
            break;
        }
    }
    // if not found:
    if !found {
        return vec![text_content(format!("Error: Memory '{memory_id}' not found"))];
    }
    // _memory_manager.save(memories)
    if let Err(e) = mgr.save(&mut memories) {
        return vec![text_content(format!("Error: {e}"))];
    }
    // if _memory_vector and _memory_vector.healthy and full_id:
    //     try: _memory_vector.remove(full_id); _memory_vector.add(full_id, new_text) except: pass
    // Reached only when a healthy store is injected (the live app path has a
    // None/unhealthy store, so this short-circuits). The remove + re-add pair is
    // wrapped in try/except: pass, so any vector error is swallowed.
    if let (Some(vec_store), Some(fid)) = (&state.memory_vector, &full_id) {
        if vec_store.healthy() {
            vec_store.remove(fid);
            let _ = vec_store.add(fid, &new_text).await;
        }
    }
    // return f"Memory updated: {new_text}"
    vec![text_content(format!("Memory updated: {new_text}"))]
}

/// `elif action == "delete":`
fn delete_action(state: &State, mgr: &MemoryManager, arguments: &Value) -> Vec<Value> {
    // memory_id = arguments.get("memory_id", "")
    let memory_id = arg_str(arguments, "memory_id");
    // if not memory_id:
    if memory_id.is_empty() {
        return vec![text_content("Error: delete needs memory_id")];
    }
    // memories = _memory_manager.load_all()
    let mut memories = mgr.load_all();
    let mut full_id: Option<String> = None;
    let mut deleted_text = String::new();
    let mut deleted_category = String::new();
    // for m in memories: if m.get("id", "").startswith(memory_id): ... break
    for m in &memories {
        let id = get_str_or(m, "id", "");
        if id.starts_with(&memory_id) {
            full_id = Some(id);
            deleted_text = get_str_or(m, "text", "");
            deleted_category = get_str_or(m, "category", "");
            break;
        }
    }
    // if not full_id:
    let full_id = match full_id {
        Some(fid) => fid,
        None => return vec![text_content(format!("Error: Memory '{memory_id}' not found"))],
    };
    // memories = [m for m in memories if m.get("id") != full_id]
    memories.retain(|m| get_str_or(m, "id", "") != full_id);
    // _memory_manager.save(memories)
    if let Err(e) = mgr.save(&mut memories) {
        return vec![text_content(format!("Error: {e}"))];
    }
    // if _memory_vector and _memory_vector.healthy and full_id:
    //     try: _memory_vector.remove(full_id) except: pass
    if let Some(vec_store) = &state.memory_vector {
        if vec_store.healthy() {
            vec_store.remove(&full_id);
        }
    }
    // cat = f"[{deleted_category}] " if deleted_category else ""
    let cat = if deleted_category.is_empty() {
        String::new()
    } else {
        format!("[{deleted_category}] ")
    };
    // snippet = deleted_text if len(deleted_text) <= 120 else deleted_text[:117] + "..."
    let snippet = if deleted_text.chars().count() <= 120 {
        deleted_text
    } else {
        format!("{}...", char_prefix(&deleted_text, 117))
    };
    // return f"Memory deleted: {cat}{snippet} (id: {memory_id})"
    vec![text_content(format!("Memory deleted: {cat}{snippet} (id: {memory_id})"))]
}

/// `elif action == "search":`
fn search_action(mgr: &MemoryManager, arguments: &Value) -> Vec<Value> {
    // query = arguments.get("text", "")
    let query = arg_str(arguments, "text");
    // if not query:
    if query.is_empty() {
        return vec![text_content("Error: search needs text (query)")];
    }
    // memories = _memory_manager.load()  (owner-unfiltered)
    let memories = mgr.load(None);
    // MemoryManager always has get_relevant_memories (the hasattr branch is
    // always true), so we take that path exactly:
    // results = _memory_manager.get_relevant_memories(query, memories, threshold=0.05, max_items=20)
    let results = mgr.get_relevant_memories(&query, &memories, 0.05, 20);
    // if not results:
    if results.is_empty() {
        return vec![text_content(format!("No memories found matching '{query}'."))];
    }
    // lines = [f"Found {len(results)} matching memories:\n"]
    let mut lines: Vec<String> = vec![format!("Found {} matching memories:\n", results.len())];
    // for m in results:
    for m in &results {
        let cat = get_str_or(m, "category", "fact");
        let mid = char_prefix(&get_str_or(m, "id", "?"), 8);
        let text = get_str_or(m, "text", "");
        lines.push(format!("- [{cat}] `{mid}` — {text}"));
    }
    vec![text_content(lines.join("\n"))]
}

/// `arguments.get(key, "")` for a string-typed argument.
fn arg_str(arguments: &Value, key: &str) -> String {
    arguments.get(key).and_then(|v| v.as_str()).unwrap_or("").to_string()
}

/// `arguments.get(key, default)` for a string-typed argument with a non-empty
/// default (e.g. `category` defaults to `"fact"`).
fn arg_str_default(arguments: &Value, key: &str, default: &str) -> String {
    match arguments.get(key) {
        Some(Value::String(s)) => s.clone(),
        // The Python `.get(key, default)` returns the default only when the key
        // is absent; a present non-string is unusual but would be used as-is.
        // The schema constrains these to strings, so absent -> default is the
        // observable case.
        _ => default.to_string(),
    }
}

/// `m.get(key, default)` returning the string value (or `default` when the key
/// is absent or not a string).
fn get_str_or(m: &Value, key: &str, default: &str) -> String {
    m.get(key).and_then(|v| v.as_str()).unwrap_or(default).to_string()
}

/// `s[:n]` over Unicode code points — Python `str` slicing operates on code
/// points, never bytes, so this takes the first `n` chars (the slice is clamped
/// to the string length, matching Python's tolerant slice).
fn char_prefix(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Wrap an already-built `State` in a `OnceCell` that is pre-initialized, so
    /// `call_tool`'s `get_or_init` returns it without rebuilding (the tests
    /// inject their own temp-dir-backed `State` rather than running
    /// `ensure_init`, which would touch the live `DATA_DIR`).
    fn cell_of(state: State) -> OnceCell<State> {
        let cell = OnceCell::new();
        cell.set(state).ok().unwrap();
        cell
    }

    /// Build a `State` with an in-memory manager pointed at a temp dir and no
    /// vector store (the live app path), seeded with the given entries, wrapped
    /// in a pre-initialized `OnceCell` for `call_tool`.
    fn state_with(entries: Vec<Value>) -> (OnceCell<State>, tempfile::TempDir) {
        let dir = tempfile::tempdir().unwrap();
        let data_dir = dir.path().to_str().unwrap();
        let mgr = MemoryManager::new(data_dir).unwrap();
        let mut entries = entries;
        mgr.save(&mut entries).unwrap();
        (
            cell_of(State { memory_manager: Some(mgr), memory_vector: None }),
            dir,
        )
    }

    fn text_of(out: &[Value]) -> String {
        out[0].get("text").and_then(|v| v.as_str()).unwrap().to_string()
    }

    #[test]
    fn list_tools_shape_matches_python() {
        let tools = tools();
        assert_eq!(tools.len(), 1);
        assert_eq!(tools[0].name, "manage_memory");
        let props = &tools[0].input_schema["properties"];
        // The action enum is exactly the five verbs.
        assert_eq!(
            props["action"]["enum"],
            json!(["list", "add", "edit", "delete", "search"])
        );
        assert_eq!(
            props["category"]["enum"],
            json!(["fact", "event", "contact", "preference"])
        );
        assert_eq!(tools[0].input_schema["required"], json!(["action"]));
    }

    #[tokio::test]
    async fn unknown_tool_name() {
        let (state, _d) = state_with(vec![]);
        let out = call_tool(&state, "not_a_tool", &json!({})).await;
        assert_eq!(text_of(&out), "Unknown tool: not_a_tool");
    }

    #[tokio::test]
    async fn manager_unavailable() {
        let state = cell_of(State { memory_manager: None, memory_vector: None });
        let out = call_tool(&state, "manage_memory", &json!({"action": "list"})).await;
        assert_eq!(text_of(&out), "Error: Memory manager not available");
    }

    #[tokio::test]
    async fn unknown_action() {
        let (state, _d) = state_with(vec![]);
        let out = call_tool(&state, "manage_memory", &json!({"action": "frobnicate"})).await;
        assert_eq!(
            text_of(&out),
            "Error: Unknown action 'frobnicate'. Use: list, add, edit, delete, search"
        );
    }

    #[tokio::test]
    async fn list_empty_no_filter() {
        let (state, _d) = state_with(vec![]);
        let out = call_tool(&state, "manage_memory", &json!({"action": "list"})).await;
        assert_eq!(text_of(&out), "No memories found.");
    }

    #[tokio::test]
    async fn list_empty_with_category_filter() {
        let (state, _d) = state_with(vec![]);
        let out = call_tool(
            &state,
            "manage_memory",
            &json!({"action": "list", "category": "event"}),
        )
        .await;
        assert_eq!(text_of(&out), "No memories found in category 'event'.");
    }

    #[tokio::test]
    async fn list_formats_entries_and_truncates_long_text() {
        let long = "x".repeat(200);
        let entries = vec![
            json!({"id": "abcdefgh12345", "text": "short fact", "category": "fact"}),
            json!({"id": "ZZZ", "text": long, "category": "event"}),
        ];
        let (state, _d) = state_with(entries);
        let out = call_tool(&state, "manage_memory", &json!({"action": "list"})).await;
        let t = text_of(&out);
        assert!(t.starts_with("Found 2 memory entries:\n"));
        // id[:8] slice and the `— ` separator.
        assert!(t.contains("- [fact] `abcdefgh` — short fact"));
        // 150-char truncation with the trailing "...".
        let expected_long = format!("- [event] `ZZZ` — {}...", "x".repeat(150));
        assert!(t.contains(&expected_long));
    }

    #[tokio::test]
    async fn list_category_filter_is_case_insensitive() {
        let entries = vec![
            json!({"id": "id1", "text": "a", "category": "Fact"}),
            json!({"id": "id2", "text": "b", "category": "event"}),
        ];
        let (state, _d) = state_with(entries);
        let out = call_tool(
            &state,
            "manage_memory",
            &json!({"action": "list", "category": "FACT"}),
        )
        .await;
        let t = text_of(&out);
        assert!(t.starts_with("Found 1 memory entries:\n"));
        assert!(t.contains("`id1`"));
    }

    #[tokio::test]
    async fn add_empty_text_rejected() {
        let (state, _d) = state_with(vec![]);
        let out = call_tool(&state, "manage_memory", &json!({"action": "add", "text": ""})).await;
        assert_eq!(text_of(&out), "Error: Memory text cannot be empty");
    }

    #[tokio::test]
    async fn add_default_category_is_fact_and_persists() {
        let (state, _d) = state_with(vec![]);
        let out = call_tool(
            &state,
            "manage_memory",
            &json!({"action": "add", "text": "buy milk"}),
        )
        .await;
        let t = text_of(&out);
        assert!(t.starts_with("Memory added: [fact] buy milk (id: "));
        assert!(t.ends_with(")"));
        // The entry is now loadable.
        let mgr = state.get().unwrap().memory_manager.as_ref().unwrap();
        let mems = mgr.load(None);
        assert_eq!(mems.len(), 1);
        assert_eq!(mems[0].get("text").and_then(|v| v.as_str()), Some("buy milk"));
        assert_eq!(mems[0].get("source").and_then(|v| v.as_str()), Some("ai_agent"));
    }

    #[tokio::test]
    async fn add_explicit_category() {
        let (state, _d) = state_with(vec![]);
        let out = call_tool(
            &state,
            "manage_memory",
            &json!({"action": "add", "text": "dentist tue", "category": "event"}),
        )
        .await;
        assert!(text_of(&out).starts_with("Memory added: [event] dentist tue (id: "));
    }

    #[tokio::test]
    async fn edit_requires_id_and_text() {
        let (state, _d) = state_with(vec![]);
        let out = call_tool(
            &state,
            "manage_memory",
            &json!({"action": "edit", "memory_id": "abc"}),
        )
        .await;
        assert_eq!(text_of(&out), "Error: edit needs memory_id and text");
    }

    #[tokio::test]
    async fn edit_not_found() {
        let entries = vec![json!({"id": "deadbeef", "text": "x", "category": "fact"})];
        let (state, _d) = state_with(entries);
        let out = call_tool(
            &state,
            "manage_memory",
            &json!({"action": "edit", "memory_id": "nope", "text": "y"}),
        )
        .await;
        assert_eq!(text_of(&out), "Error: Memory 'nope' not found");
    }

    #[tokio::test]
    async fn edit_by_prefix_updates_text() {
        let entries = vec![json!({"id": "abc12345xyz", "text": "old", "category": "fact"})];
        let (state, _d) = state_with(entries);
        let out = call_tool(
            &state,
            "manage_memory",
            &json!({"action": "edit", "memory_id": "abc", "text": "new value"}),
        )
        .await;
        assert_eq!(text_of(&out), "Memory updated: new value");
        let mgr = state.get().unwrap().memory_manager.as_ref().unwrap();
        let mems = mgr.load(None);
        assert_eq!(mems[0].get("text").and_then(|v| v.as_str()), Some("new value"));
    }

    #[tokio::test]
    async fn delete_requires_id() {
        let (state, _d) = state_with(vec![]);
        let out = call_tool(&state, "manage_memory", &json!({"action": "delete"})).await;
        assert_eq!(text_of(&out), "Error: delete needs memory_id");
    }

    #[tokio::test]
    async fn delete_not_found() {
        let (state, _d) = state_with(vec![]);
        let out = call_tool(
            &state,
            "manage_memory",
            &json!({"action": "delete", "memory_id": "zzz"}),
        )
        .await;
        assert_eq!(text_of(&out), "Error: Memory 'zzz' not found");
    }

    #[tokio::test]
    async fn delete_by_prefix_with_category_and_short_snippet() {
        let entries = vec![json!({"id": "cafef00d99", "text": "remember this", "category": "fact"})];
        let (state, _d) = state_with(entries);
        let out = call_tool(
            &state,
            "manage_memory",
            &json!({"action": "delete", "memory_id": "cafe"}),
        )
        .await;
        // cat prefix + snippet (<=120 so verbatim) + the raw memory_id selector.
        assert_eq!(text_of(&out), "Memory deleted: [fact] remember this (id: cafe)");
        let mgr = state.get().unwrap().memory_manager.as_ref().unwrap();
        assert!(mgr.load(None).is_empty());
    }

    #[tokio::test]
    async fn delete_long_text_snippet_truncated_at_117() {
        let long = "y".repeat(200);
        let entries = vec![json!({"id": "id000001", "text": long, "category": ""})];
        let (state, _d) = state_with(entries);
        let out = call_tool(
            &state,
            "manage_memory",
            &json!({"action": "delete", "memory_id": "id00"}),
        )
        .await;
        // Empty category -> no `[..] ` prefix; snippet is text[:117] + "...".
        let expected = format!("Memory deleted: {}... (id: id00)", "y".repeat(117));
        assert_eq!(text_of(&out), expected);
    }

    #[tokio::test]
    async fn delete_is_exact_single_match_not_prefix_bulk() {
        // Two entries share the "cafe" prefix. The Python delete resolves the
        // FIRST matching entry's full id, then deletes exactly that one
        // (`m.get("id") != full_id`) — it must NOT bulk-delete every prefix match.
        let entries = vec![
            json!({"id": "cafe1111", "text": "first", "category": "fact"}),
            json!({"id": "cafe2222", "text": "second", "category": "event"}),
        ];
        let (state, _d) = state_with(entries);
        let out = call_tool(
            &state,
            "manage_memory",
            &json!({"action": "delete", "memory_id": "cafe"}),
        )
        .await;
        // The first-matched entry ("cafe1111") is the one reported and deleted.
        assert_eq!(text_of(&out), "Memory deleted: [fact] first (id: cafe)");
        // Only the single resolved entry is gone; the other prefix match survives.
        let mgr = state.get().unwrap().memory_manager.as_ref().unwrap();
        let mems = mgr.load(None);
        assert_eq!(mems.len(), 1);
        assert_eq!(mems[0].get("id").and_then(|v| v.as_str()), Some("cafe2222"));
    }

    #[tokio::test]
    async fn search_requires_query() {
        let (state, _d) = state_with(vec![]);
        let out = call_tool(&state, "manage_memory", &json!({"action": "search", "text": ""})).await;
        assert_eq!(text_of(&out), "Error: search needs text (query)");
    }

    #[tokio::test]
    async fn search_no_matches() {
        let entries = vec![json!({"id": "id1", "text": "completely unrelated stuff", "category": "fact"})];
        let (state, _d) = state_with(entries);
        let out = call_tool(
            &state,
            "manage_memory",
            &json!({"action": "search", "text": "zzqqxx"}),
        )
        .await;
        assert_eq!(text_of(&out), "No memories found matching 'zzqqxx'.");
    }

    #[tokio::test]
    async fn search_returns_matches() {
        let entries = vec![
            json!({"id": "aabbccdd", "text": "the deadline is friday", "category": "event"}),
        ];
        let (state, _d) = state_with(entries);
        let out = call_tool(
            &state,
            "manage_memory",
            &json!({"action": "search", "text": "deadline"}),
        )
        .await;
        let t = text_of(&out);
        assert!(t.starts_with("Found 1 matching memories:\n"));
        // id[:8] and full (un-truncated) text in the search formatting.
        assert!(t.contains("- [event] `aabbccdd` — the deadline is friday"));
    }
}
