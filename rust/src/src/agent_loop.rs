// src/agent_loop.rs  <- src/agent_loop.py
//! Streaming agent loop for odysseus-ui.
//!
//! Wraps `crate::src::llm_core::stream_llm` (via `stream_llm_with_fallback`) with
//! multi-round tool execution. The LLM decides when to use tools by writing
//! fenced code blocks or emitting native OpenAI `tool_calls`.
//!
//! SHAPE: `stream_agent_loop` is a PLAIN fn (not `async`, not a `Future`) that
//! returns `impl Stream<Item = String>` built with `async_stream::stream!{}`,
//! exactly like `llm_core::stream_llm`. Consume it with
//! `futures_util::StreamExt`. The 17-param Python signature is folded into one
//! owned [`AgentLoopArgs`] struct (with a `Default` impl mirroring the Python
//! kwarg defaults) to keep call sites ergonomic.
//!
//! SSE STRING VOCABULARY: each yielded item is one already-framed SSE string
//! ending in `"\n\n"`, byte-identical to `stream_llm`'s wire format and to what
//! `chat_routes` parses. The loop CONSUMES `stream_llm_with_fallback`'s stream,
//! parses the inner SSE strings (`data:{delta..}` / `{type:tool_call_delta |
//! tool_calls | usage}`, `event: error`, `data:[DONE]`), withholds the inner
//! `[DONE]` until all rounds finish, and re-emits the agent vocabulary
//! (`agent_prep`, `tool_start`, `tool_progress`, `tool_output`, `agent_step`,
//! `web_sources`, `doc_stream_*`, `doc_update`, `doc_suggestions`, `ui_control`,
//! `budget_exceeded`, `metrics`, a final single `data: [DONE]`).
//!
//! FEATURE GATING (per the contract `shared_decisions`): the pure helpers
//! (`_detect_admin_intent`, `_extract_last_user_message`,
//! `_recent_context_for_retrieval`, `_section_text`, `_assemble_prompt`,
//! `get_builtin_overrides`, `_build_actions_snapshot`, `_compute_final_metrics`,
//! `_resolve_tool_blocks`, `_append_tool_results`, the prompt consts +
//! `TOOL_SECTIONS` + the keyword sets) and the async/db surface
//! (`stream_agent_loop`, `_build_system_prompt`, `_build_base_prompt`,
//! `_run_verifier_subagent`, `_load_mcp_disabled_map`) are all always compiled —
//! the crate has no cargo feature flags — and the pure-logic unit tests run
//! alongside them.
//!
//! DOCUMENTED DEVIATIONS:
//!   * The unported subsystems the Python loop reaches through best-effort
//!     `try/except` blocks (`src.settings` / `get_setting`, `src.tool_index`
//!     RAG, `services.memory.skills`, `src.integrations`, `src.teacher_escalation`,
//!     `src.pdf_form_doc`, `routes.prefs_routes`) are honest no-ops here: settings
//!     reads return the Python DEFAULTS inline (`get_setting`), the RAG index is
//!     unavailable so we always take the keyword-fallback tool-selection path
//!     (the Python `get_tool_index()`→`None` branch), and skills / integrations /
//!     teacher-escalation / pdf-form injection are skipped (their Python
//!     counterparts are wrapped in `try/except … logger.debug("non-fatal")`).
//!   * The MCP manager (`agent_tools::get_mcp_manager`) now holds the live
//!     `Arc<McpManager>` wired in at startup, so `mcp_schemas` is populated from
//!     `get_all_openai_schemas` and the MCP-prompt/schema injection is active
//!     (see `agent_loop_web`). When no manager is set (or MCP is suppressed for a
//!     public/non-admin owner) the schema list is empty — the honest soft-fail,
//!     never a fabricated entry.
//!   * `active_document` is modeled as the owned [`ActiveDocument`] struct (the
//!     Python passes a SQLAlchemy `Document`; the chat_routes wiring constructs
//!     this from the DB row).
//!   * Python `round()` uses banker's rounding for `elapsed_s` / metrics; the
//!     Rust port rounds half-away-from-zero. Cosmetic only.

// The agent-loop entry points mirror Python signatures that thread 8-12 positional
// params (url/model/headers/tools/session/owner/limits/…). Re-bundling them into
// structs would diverge from the 1:1 Python call sites for no behavioral gain, so
// the lint is allowed module-wide rather than per-function.
#![allow(clippy::too_many_arguments)]

use std::collections::{HashSet, VecDeque};
use std::sync::Mutex;

use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Map, Value};

use crate::pylog as logger;
use crate::src::agent_tools::{ToolBlock, MAX_AGENT_ROUNDS};
use crate::src::prompt_security::untrusted_context_message;
use crate::src::tool_implementations::{set_active_document, set_active_model};

// ---------------------------------------------------------------------------
// AgentLoopArgs — the single owned args struct (folds the 17-param signature).
// ---------------------------------------------------------------------------

/// An open editor document the agent is operating on. Python passes a SQLAlchemy
/// `Document`; the Rust port models the four attributes the loop reads
/// (`.id`, `.current_content`, `.title`, `.language`).
#[derive(Clone, Debug, Default)]
pub struct ActiveDocument {
    pub id: String,
    pub current_content: Option<String>,
    pub title: Option<String>,
    pub language: Option<String>,
}

/// Owned arguments for [`stream_agent_loop`]. The `Default` impl mirrors the
/// Python kwarg defaults.
#[derive(Clone)]
pub struct AgentLoopArgs {
    pub endpoint_url: String,
    pub model: String,
    pub messages: Vec<Value>,
    /// `headers` — `None` -> empty map.
    pub headers: indexmap::IndexMap<String, String>,
    pub temperature: f64,
    pub max_tokens: i64,
    pub prompt_type: Option<String>,
    pub max_rounds: i64,
    /// `0` = unlimited.
    pub max_tool_calls: i64,
    pub context_length: i64,
    pub active_document: Option<ActiveDocument>,
    pub session_id: Option<String>,
    pub disabled_tools: Option<HashSet<String>>,
    pub owner: Option<String>,
    pub relevant_tools: Option<HashSet<String>>,
    /// `[(url, model, headers)]`.
    // Mirrors the Python `[(url, model, headers)]` fallback tuple shape 1:1.
    #[allow(clippy::type_complexity)]
    pub fallbacks: Option<Vec<(String, String, indexmap::IndexMap<String, String>)>>,
    /// Python `_is_teacher_run`.
    pub is_teacher_run: bool,
}

impl Default for AgentLoopArgs {
    fn default() -> Self {
        AgentLoopArgs {
            endpoint_url: String::new(),
            model: String::new(),
            messages: Vec::new(),
            headers: indexmap::IndexMap::new(),
            temperature: 0.3,
            max_tokens: 4096,
            prompt_type: None,
            max_rounds: MAX_AGENT_ROUNDS,
            max_tool_calls: 0,
            context_length: 0,
            active_document: None,
            session_id: None,
            disabled_tools: None,
            owner: None,
            relevant_tools: None,
            fallbacks: None,
            is_teacher_run: false,
        }
    }
}

// ---------------------------------------------------------------------------
// settings reads — `get_setting(key, default)` for the handful of keys the loop
// reads. Backed by the ported `crate::src::settings` (reads the SAME
// `settings.json` the Python `src.settings` does), so user-customized values
// take effect (formerly hardcoded to the Python defaults).
// ---------------------------------------------------------------------------
//
// Python keys + defaults (`agent_loop.py`):
//   image_gen_enabled                -> True   (`if not get_setting(...)`)
//   agent_input_token_budget         -> 6000   (`int(get_setting(...) or 0)`)
//   agent_stream_timeout_seconds     -> 300    (`int(get_setting(...) or 300)`)
//   agent_verifier_subagent          -> False  (used as a truthy `and` guard)
//   builtin_tool_overrides           -> {}

/// CPython truthiness for a JSON value (`bool(x)`): null/false/0/""/[]/{} -> false.
fn json_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `int(x)` truncation for a settings value (only called on truthy values, so the
/// `int(x or default)` short-circuit has already chosen `x`).
fn json_int_trunc(v: &Value) -> i64 {
    match v {
        Value::Number(n) => n
            .as_i64()
            .or_else(|| n.as_f64().map(|f| f.trunc() as i64))
            .unwrap_or(0),
        Value::String(s) => s.trim().parse::<f64>().map(|f| f.trunc() as i64).unwrap_or(0),
        Value::Bool(true) => 1,
        _ => 0,
    }
}

fn setting_image_gen_enabled() -> bool {
    // `if not get_setting("image_gen_enabled", True)` -> enabled iff truthy.
    json_truthy(&crate::src::settings::get_setting(
        "image_gen_enabled",
        Value::Bool(true),
    ))
}
fn setting_agent_input_token_budget() -> i64 {
    // `int(get_setting("agent_input_token_budget", 6000) or 0)`.
    let v = crate::src::settings::get_setting("agent_input_token_budget", json!(6000));
    if json_truthy(&v) {
        json_int_trunc(&v)
    } else {
        0
    }
}
fn setting_agent_stream_timeout_seconds() -> i64 {
    // `int(get_setting("agent_stream_timeout_seconds", 300) or 300)`.
    let v = crate::src::settings::get_setting("agent_stream_timeout_seconds", json!(300));
    if json_truthy(&v) {
        json_int_trunc(&v)
    } else {
        300
    }
}
fn setting_agent_verifier_subagent() -> bool {
    // `... and get_setting("agent_verifier_subagent", False)` -> truthy guard.
    json_truthy(&crate::src::settings::get_setting(
        "agent_verifier_subagent",
        Value::Bool(false),
    ))
}

// ---------------------------------------------------------------------------
// System-prompt constants (verbatim from agent_loop.py)
// ---------------------------------------------------------------------------

/// `_AGENT_PREAMBLE`.
const _AGENT_PREAMBLE: &str = "You are an AI assistant with tool access. You can run shell commands, execute Python, search the web, read/write files, create and edit documents, generate images, manage memories, and more. To use a tool, write a fenced code block with the tool name as the language tag. The block executes automatically and you see the output.";

/// `_AGENT_RULES`.
const _AGENT_RULES: &str = include_str!("agent_loop_rules.txt");

/// `_API_AGENT_RULES`.
const _API_AGENT_RULES: &str = include_str!("agent_loop_api_rules.txt");

include!("agent_loop_sections.rs");

// ---------------------------------------------------------------------------
// builtin overrides + section text
// ---------------------------------------------------------------------------

/// `get_builtin_overrides()` — user overrides for built-in tool descriptions
/// (`TOOL_SECTIONS`), stored globally in `settings.json` under
/// `builtin_tool_overrides` so the user can preview/edit how the assistant is
/// told to use a native tool.
///
/// ```python
/// ov = get_setting("builtin_tool_overrides", {})
/// return ov if isinstance(ov, dict) else {}
/// ```
pub fn get_builtin_overrides() -> Map<String, Value> {
    match crate::src::settings::get_setting("builtin_tool_overrides", json!({})) {
        Value::Object(m) => m,
        // `return ov if isinstance(ov, dict) else {}`
        _ => Map::new(),
    }
}

/// Public read-only accessor for the module-private `TOOL_SECTIONS` map. Lets
/// other modules (e.g. `routes::skills_routes`'s `/builtin*` handlers) iterate
/// the shipped built-in tool descriptions — the same `name -> default text`
/// pairs the system prompt is assembled from. The Python source reaches these by
/// importing `src.agent_loop.TOOL_SECTIONS` directly.
pub fn tool_sections() -> &'static indexmap::IndexMap<&'static str, &'static str> {
    &TOOL_SECTIONS
}

/// `_section_text(name, default)` — effective `TOOL_SECTIONS` text for a tool:
/// user override if set, else the shipped default.
pub fn _section_text(name: &str, default: &str) -> String {
    let ov = get_builtin_overrides();
    match ov.get(name).and_then(|v| v.as_str()) {
        Some(s) if !s.trim().is_empty() => s.to_string(),
        _ => default.to_string(),
    }
}

/// `_assemble_prompt(tool_names, disabled_tools=None, compact=False)` — build the
/// system prompt with only the specified tools included.
pub fn _assemble_prompt(
    tool_names: &HashSet<String>,
    disabled_tools: Option<&HashSet<String>>,
    compact: bool,
) -> String {
    let disabled: HashSet<String> = disabled_tools.cloned().unwrap_or_default();
    // included = tool_names - disabled
    let included: HashSet<String> = tool_names.difference(&disabled).cloned().collect();

    if compact {
        // tool_list = ", ".join(sorted(included)) if included else "none"
        let tool_list = if included.is_empty() {
            "none".to_string()
        } else {
            let mut v: Vec<&String> = included.iter().collect();
            v.sort();
            v.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", ")
        };
        let parts = [
            "You are an AI assistant with tool access.".to_string(),
            format!("Available tools: {tool_list}."),
            _API_AGENT_RULES.to_string(),
        ];
        return parts.join("\n\n");
    }

    let mut parts: Vec<String> = vec![_AGENT_PREAMBLE.to_string()];

    let mut full_blocks: Vec<String> = Vec::new();
    let mut one_liners: Vec<String> = Vec::new();

    // Iterate TOOL_SECTIONS in insertion order (IndexMap preserves the Python
    // dict order, which is observable in the assembled prompt).
    for (name, default_section) in TOOL_SECTIONS.iter() {
        if !included.contains(*name) {
            continue;
        }
        let section = _section_text(name, default_section);
        if section.starts_with("```") || section.starts_with('-') {
            if section.starts_with("- ") {
                one_liners.push(section);
            } else {
                full_blocks.push(section);
            }
        }
    }

    if !full_blocks.is_empty() {
        parts.push(full_blocks.join("\n\n"));
    }
    if !one_liners.is_empty() {
        parts.push(format!("## Additional tools\n{}", one_liners.join("\n")));
    }

    // Mention tools that exist but weren't included.
    let all_known: HashSet<String> = TOOL_SECTIONS.keys().map(|k| k.to_string()).collect();
    // not_shown = all_known - included - disabled
    let not_shown: HashSet<String> = all_known
        .difference(&included)
        .cloned()
        .collect::<HashSet<String>>()
        .difference(&disabled)
        .cloned()
        .collect();
    if !not_shown.is_empty() {
        let mut sorted: Vec<&String> = not_shown.iter().collect();
        sorted.sort();
        let sample: Vec<&String> = sorted.iter().take(5).copied().collect();
        let mut hint = sample.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", ");
        if not_shown.len() > 5 {
            hint += &format!(", ... ({} more)", not_shown.len() - 5);
        }
        parts.push(format!("(Other tools available when needed: {hint})"));
    }

    parts.push(_AGENT_RULES.to_string());
    parts.join("\n\n")
}

// ---------------------------------------------------------------------------
// Constants — hosts / keyword sets / admin tool sets
// ---------------------------------------------------------------------------

/// `_API_HOSTS` — hosts whose endpoints natively support OpenAI-style function
/// calling.
static _API_HOSTS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "api.openai.com", "api.anthropic.com",
        "openrouter.ai", "api.groq.com",
        "api.mistral.ai", "api.cohere.com",
        "api.deepseek.com", "deepseek.com",
        "api.together.xyz", "api.fireworks.ai",
        "api.perplexity.ai", "api.x.ai",
        "ollama.com", "api.venice.ai",
        // Local OpenAI-compatible endpoints (llama.cpp, vLLM, LM Studio, etc.).
        // Without these, `_is_api_model` falls back to keyword sniffing on the
        // model name, so well-behaved local servers don't get native tool
        // schemas and the agent silently degrades to fenced-block parsing.
        "localhost", "127.0.0.1", "host.docker.internal",
    ]
    .into_iter()
    .collect()
});

/// `_MCP_KEYWORDS`.
static _MCP_KEYWORDS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "browse", "browser", "website", "calendar", "event", "email",
        "gmail", "screenshot", "navigate", "click", "miniflux", "rss", "feed",
    ]
    .into_iter()
    .collect()
});

/// `_ADMIN_SCHEMA_NAMES`.
static _ADMIN_SCHEMA_NAMES: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "manage_session", "manage_skills", "manage_tasks",
        "manage_endpoints", "manage_mcp", "manage_webhooks", "manage_tokens",
        "create_session", "list_sessions", "send_to_session", "pipeline",
        "ask_teacher", "list_models", "search_chats",
    ]
    .into_iter()
    .collect()
});

const _TOOL_SELECTION_TIMEOUT_SECONDS: f64 = 1.5;

/// `_ADMIN_KEYWORDS` — admin tool keywords (ordered, list semantics).
static _ADMIN_KEYWORDS: Lazy<Vec<&'static str>> = Lazy::new(|| {
    vec![
        "session", "sessions", "chat", "chats", "conversation", "conversations",
        "delete", "fork", "truncate",
        "archive", "rename", "endpoint", "endpoints", "api key",
        "webhook", "webhooks", "token", "tokens", "mcp", "server", "skill", "skills",
        "task", "tasks", "schedule", "cron", "setting", "settings", "preference",
        "configure", "config", "setup", "manage", "admin", "pipeline", "second opinion",
        "list models", "switch model", "change model", "theme", "create theme",
        "document", "documents", "doc", "docs", "library", "tidy",
        "note", "notes", "todo", "todos", "reminder", "reminders",
    ]
});

/// `_ADMIN_TOOLS`.
static _ADMIN_TOOLS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "manage_session", "manage_skills", "manage_tasks",
        "manage_endpoints", "manage_mcp", "manage_webhooks", "manage_tokens",
        "manage_documents", "manage_settings", "create_session", "list_sessions",
        "send_to_session", "pipeline", "ask_teacher", "list_models",
    ]
    .into_iter()
    .collect()
});

// ── tool_index keyword fallback (ported from `ToolIndex._KEYWORD_HINTS` +
// `ALWAYS_AVAILABLE`). The RAG index is unported, so the loop always takes the
// keyword-fallback selection path (the Python `get_tool_index()`→`None` branch).

/// `tool_index.ALWAYS_AVAILABLE`.
static ALWAYS_AVAILABLE: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "bash", "python", "web_search", "read_file",
        "api_call",
        "list_served_models", "stop_served_model",
        "app_api",
    ]
    .into_iter()
    .collect()
});

/// `ToolIndex._KEYWORD_HINTS` — `(keywords, tools)` pairs (a Vec preserves the
/// source order; matching is order-independent so any structure works, but Vec
/// keeps the literal source layout for review).
static _KEYWORD_HINTS: Lazy<Vec<(Vec<&'static str>, Vec<&'static str>)>> = Lazy::new(|| {
    vec![
        (vec!["email", "mail", "gmail", "googlemail", "message", "send", "reply", "inbox", "unread", "tell"],
         vec!["list_email_accounts", "list_emails", "read_email", "send_email", "reply_to_email", "bulk_email", "delete_email", "archive_email", "mark_email_read", "resolve_contact", "ui_control"]),
        (vec!["calendar", "event", "meeting", "schedule", "appointment"],
         vec!["manage_calendar"]),
        (vec!["note", "todo", "reminder", "remind", "checklist", "remember to"],
         vec!["manage_notes"]),
        (vec!["sessions", "my chats", "these chats", "those chats", "chat history", "rename chat", "rename session", "rename the chat", "rename my chat", "rename the session", "archive chat", "archive session", "delete chat", "delete session", "fork chat", "fork session", "name the chats", "name my chats", "rename them"],
         vec!["list_sessions", "manage_session"]),
        (vec!["recurring", "every day", "every hour", "every morning", "every evening", "every night", "every week", "each morning", "daily task", "background task", "scheduled task", "schedule a", "automatically", "auto-summarize", "auto summarize", "cron", "periodically", "on a schedule", "set up a task", "create a task", "summarize my inbox every", "remind me every"],
         vec!["manage_tasks"]),
        (vec!["contact", "address", "phone", "who is"],
         vec!["resolve_contact", "manage_contact"]),
        (vec!["save contact", "add contact", "new contact", "update contact", "edit contact", "delete contact", "remove contact", "save this person", "add to contacts", "save to contacts"],
         vec!["manage_contact"]),
        (vec!["ask gpt", "ask claude", "ask gemini", "ask deepseek", "ask minimax", "ask qwen", "ask the", "ask another model", "what does", "what would", "second opinion", "other model", "different model", "compare answers", "compare models", "delegate to", "have model"],
         vec!["chat_with_model", "ask_teacher", "list_models"]),
        (vec!["research", "reserach", "reasearch", "look into", "investigate", "deep dive", "deep research", "find out about", "study up on", "report on", "do research", "look up everything"],
         vec!["trigger_research"]),
        (vec!["change my", "set my", "use the voice", "change the voice", "my voice", "tts voice", "search engine", "default model", "teacher model", "task model", "background model", "image quality", "reminder channel", "send reminders to", "remind me by", "speak faster", "speak slower", "agent timeout", "token budget", "max tool calls", "use this model for", "use that model for", "my settings", "change setting", "change a setting", "set setting", "preference", "preferences", "configure"],
         vec!["manage_settings", "ui_control"]),
        (vec!["my research", "the research", "research on", "open research", "read research", "find research", "delete research", "remove research", "list research", "my reports", "the report", "saved research", "research library", "past research", "research i did", "research about"],
         vec!["manage_research", "trigger_research"]),
        (vec!["edit", "change", "fix", "rewrite", "update", "replace", "add a", "tweak", "modify", "rename", "paragraph", "section", "line", "the doc", "the document", "in the doc"],
         vec!["edit_document", "update_document", "create_document", "suggest_document"]),
        (vec!["delete this doc", "delete the doc", "delete document", "remove document", "remove the doc", "trash", "list documents", "list docs", "all my docs", "my documents", "my docs", "my files", "open the", "open my", "open document", "open doc", "find the", "find my", "find document", "read the", "read my", "show me the", "show my", "the file", "my file", "the report", "the write-up", "the writeup", "saved document", "in my library", "in the library"],
         vec!["manage_documents", "edit_document"]),
        (vec!["theme", "color scheme", "colors of the ui", "make it dark", "make it light", "make the ui", "switch theme", "change theme", "dark mode", "light mode", "toggle"],
         vec!["ui_control"]),
        (vec!["cookbook", "kill cookbook", "stop cookbook", "stop the model", "kill the model", "kill my model", "what's running", "what is running", "whats running", "running models", "running model", "running server", "shut down vllm", "shutdown vllm", "stop vllm", "stop serving", "kill serve", "cancel serve"],
         vec!["list_served_models", "stop_served_model"]),
        (vec!["serve", "launch", "spin up", "start the model", "run the model", "preset", "presets", "which server", "what servers", "gpu box", "cookbook server", "vllm", "on the server", "on the gpu"],
         vec!["serve_preset", "serve_model", "list_serve_presets", "list_cookbook_servers", "list_cached_models"]),
        (vec!["download", "downloading", "downloads", "cancel download", "stop download", "kill download", "what's downloading", "download progress", "pull model", "grab model"],
         vec!["list_downloads", "cancel_download", "download_model", "list_cookbook_servers"]),
        (vec!["huggingface", "hugging face", "hf search", "find a model", "search models", "search for a model", "models for", "best model for"],
         vec!["search_hf_models", "list_cached_models"]),
        (vec!["cached models", "list models", "my models", "what models do i have", "is it downloaded", "do i have", "already downloaded", "on disk"],
         vec!["list_cached_models", "search_hf_models"]),
        (vec!["turn off", "turn on", "disable", "enable", "shell off", "shell on", "search off", "search on", "research off", "research on", "incognito", "switch model", "change model", "set mode", "agent mode", "chat mode", "open library", "open documents", "open gallery", "open email", "open inbox", "open settings", "open memories", "open memory", "open skills", "open notes", "open chats", "open sessions", "show library", "show gallery", "show inbox", "show settings", "show memory", "show memories", "show skills", "show notes", "show chats", "show sessions", "show documents"],
         vec!["ui_control"]),
        (vec!["write a", "create a doc", "draft", "compose", "poem", "story", "essay", "outline", "letter"],
         vec!["create_document", "edit_document", "update_document"]),
    ]
});

// ---------------------------------------------------------------------------
// Pure helpers
// ---------------------------------------------------------------------------

/// Extract the text of a message `content` value, joining `[{text:..}]` blocks
/// (Python's `" ".join(b.get("text","") for b in content if isinstance(b, dict))`).
fn message_content_text(content: &Value) -> String {
    match content {
        Value::String(s) => s.clone(),
        Value::Array(blocks) => blocks
            .iter()
            .filter_map(|b| b.as_object())
            .map(|b| b.get("text").and_then(|t| t.as_str()).unwrap_or(""))
            .collect::<Vec<_>>()
            .join(" "),
        _ => String::new(),
    }
}

/// `_detect_admin_intent(messages)` — does the last user message suggest
/// admin/management tool usage?
pub fn _detect_admin_intent(messages: &[Value]) -> bool {
    for msg in messages.iter().rev() {
        if msg.get("role").and_then(|r| r.as_str()) == Some("user") {
            let content = msg.get("content").unwrap_or(&Value::Null);
            let content_lower = message_content_text(content).to_lowercase();
            return _ADMIN_KEYWORDS.iter().any(|kw| content_lower.contains(kw));
        }
    }
    false
}

/// `_extract_last_user_message(messages)` — most recent user message as text.
pub fn _extract_last_user_message(messages: &[Value]) -> String {
    for msg in messages.iter().rev() {
        if msg.get("role").and_then(|r| r.as_str()) == Some("user") {
            return message_content_text(msg.get("content").unwrap_or(&Value::Null));
        }
    }
    String::new()
}

/// `_recent_context_for_retrieval(messages, max_user=3, max_chars=600)`.
pub fn _recent_context_for_retrieval(messages: &[Value], max_user: usize, max_chars: usize) -> String {
    let mut collected: Vec<String> = Vec::new();
    for msg in messages.iter().rev() {
        if msg.get("role").and_then(|r| r.as_str()) != Some("user") {
            continue;
        }
        let content = message_content_text(msg.get("content").unwrap_or(&Value::Null));
        let content = content.trim().to_string();
        // Skip injected tool-result envelopes.
        if content.is_empty() || content.starts_with("[Tool execution results]") {
            continue;
        }
        collected.push(content);
        if collected.len() >= max_user {
            break;
        }
    }
    let joined = collected.join("\n");
    // [:max_chars] over code points.
    joined.chars().take(max_chars).collect()
}

/// `_resolve_tool_blocks(round_response, native_tool_calls, round_num)` — choose
/// native function calls or fenced code block parsing. Returns
/// `(tool_blocks, used_native)`.
///
/// `native_tool_calls` entries are the accumulated `{"id","name","arguments"}`
/// maps from `stream_llm`'s `tool_calls` event.
pub fn _resolve_tool_blocks(
    round_response: &str,
    native_tool_calls: &[Value],
    round_num: i64,
) -> (Vec<ToolBlock>, bool) {
    use crate::src::tool_parsing::parse_tool_blocks;
    use crate::src::tool_schemas::function_call_to_tool_block;

    let mut used_native = false;
    let mut tool_blocks: Vec<ToolBlock> = Vec::new();
    if !native_tool_calls.is_empty() {
        for tc in native_tool_calls {
            let tc_name = tc.get("name").and_then(|n| n.as_str()).unwrap_or("");
            let tc_args = tc.get("arguments").and_then(|a| a.as_str()).unwrap_or("{}");
            match function_call_to_tool_block(tc_name, tc_args) {
                Some(block) => {
                    logger::info(&format!("  -> converted: {tc_name} -> {}", block.tool_type));
                    tool_blocks.push(block);
                }
                None => {
                    let preview: String = tc_args.chars().take(200).collect();
                    logger::warning(&format!(
                        "  -> FAILED to convert native call: {tc_name} args={preview}"
                    ));
                }
            }
        }
        if !tool_blocks.is_empty() {
            used_native = true;
        }
    }
    if !used_native {
        tool_blocks = parse_tool_blocks(round_response);
        if !tool_blocks.is_empty() {
            logger::info(&format!(
                "Agent round {round_num}: {} fenced tool block(s) detected",
                tool_blocks.len()
            ));
        }
    }

    let resp_preview: String = if round_response.is_empty() {
        "(empty)".to_string()
    } else {
        round_response.chars().take(200).collect::<String>().replace('\n', "\\n")
    };
    logger::info(&format!(
        "Agent round {round_num} summary: {} chars, {} native calls, {} tool blocks. Preview: {resp_preview}",
        round_response.chars().count(),
        native_tool_calls.len(),
        tool_blocks.len()
    ));

    (tool_blocks, used_native)
}

/// `_append_tool_results(...)` — append tool execution results back into the
/// message history for the next LLM round.
///
/// `round_reasoning` (DeepSeek / vLLM reasoning-parser deltas) is echoed back via
/// `reasoning_content` on the assistant message — DeepSeek's API rejects
/// follow-up requests in thinking mode that don't include the prior reasoning.
///
/// NOTE: it is NOT universally ignored. Nemotron's chat template re-injects EVERY
/// prior `reasoning_content` as a `<think>` block, and this agent loop is trimmed
/// only once (before the loop), so across rounds the reasoning piles up unbounded
/// — bloating context and feeding the model its own prior reasoning, which
/// reinforces repetition/looping. So keep `reasoning_content` on the MOST RECENT
/// assistant turn only: enough for DeepSeek continuity, without the per-round
/// accumulation.
pub fn _append_tool_results(
    messages: &mut Vec<Value>,
    round_response: &str,
    native_tool_calls: &[Value],
    tool_results: &[String],
    tool_result_texts: &[String],
    used_native: bool,
    round_num: i64,
    round_reasoning: &str,
) {
    // Strip reasoning_content from earlier assistant turns; only the newest keeps it.
    for m in messages.iter_mut() {
        if m.get("role").and_then(|r| r.as_str()) == Some("assistant") {
            if let Some(obj) = m.as_object_mut() {
                obj.remove("reasoning_content");
            }
        }
    }
    if used_native && !native_tool_calls.is_empty() {
        let mut assistant_msg = Map::new();
        assistant_msg.insert("role".to_string(), Value::String("assistant".to_string()));
        // When the model emitted ONLY tool calls (no prose), content must be
        // null, NOT an empty string. Google Gemini's OpenAI-compatible endpoint
        // and Ollama both reject an assistant message that carries tool_calls
        // alongside empty-string content with HTTP 400 ("contents is not
        // specified" / a JSON parse error), which aborts every tool-using turn
        // at the follow-up round. null (i.e. omitted text) is the spec-correct
        // form the OpenAI SDK itself emits, and OpenAI/Anthropic accept it too.
        // content = round_response if round_response.strip() else None
        let content = if round_response.trim().is_empty() {
            Value::Null
        } else {
            Value::String(round_response.to_string())
        };
        assistant_msg.insert("content".to_string(), content);
        if !round_reasoning.is_empty() {
            assistant_msg.insert(
                "reasoning_content".to_string(),
                Value::String(round_reasoning.to_string()),
            );
        }
        let tool_calls: Vec<Value> = native_tool_calls
            .iter()
            .enumerate()
            .map(|(j, tc)| {
                let id = tc
                    .get("id")
                    .and_then(|x| x.as_str())
                    .filter(|s| !s.is_empty())
                    .map(|s| s.to_string())
                    .unwrap_or_else(|| format!("call_{round_num}_{j}"));
                let mut call = json!({
                    "id": id,
                    "type": "function",
                    "function": {
                        "name": tc.get("name").and_then(|n| n.as_str()).unwrap_or(""),
                        "arguments": tc.get("arguments").and_then(|a| a.as_str()).unwrap_or("{}"),
                    },
                });
                // Gemini 3 requires the opaque thought_signature it returned with
                // each function call to be echoed back on the follow-up turn, or
                // the next request 400s. Replay it when present; other providers
                // never emit it (their payload builders just ignore the field).
                if let Some(extra) = tc.get("extra_content") {
                    if !extra.is_null() && json_truthy(extra) {
                        if let Some(obj) = call.as_object_mut() {
                            obj.insert("extra_content".to_string(), extra.clone());
                        }
                    }
                }
                call
            })
            .collect();
        assistant_msg.insert("tool_calls".to_string(), Value::Array(tool_calls));
        messages.push(Value::Object(assistant_msg));
        for (j, tc) in native_tool_calls.iter().enumerate() {
            let result_text = tool_result_texts.get(j).cloned().unwrap_or_default();
            let id = tc
                .get("id")
                .and_then(|x| x.as_str())
                .filter(|s| !s.is_empty())
                .map(|s| s.to_string())
                .unwrap_or_else(|| format!("call_{round_num}_{j}"));
            messages.push(json!({
                "role": "tool",
                "tool_call_id": id,
                "content": result_text,
            }));
        }
    } else {
        let tool_output_text = tool_results.join("\n\n");
        let mut msg = Map::new();
        msg.insert("role".to_string(), Value::String("assistant".to_string()));
        msg.insert("content".to_string(), Value::String(round_response.to_string()));
        if !round_reasoning.is_empty() {
            msg.insert(
                "reasoning_content".to_string(),
                Value::String(round_reasoning.to_string()),
            );
        }
        messages.push(Value::Object(msg));
        messages.push(json!({
            "role": "user",
            "content": format!("[Tool execution results]\n\n{tool_output_text}"),
        }));
    }
}

/// `round(x, n)` half-away-from-zero (documented cosmetic drift vs Python's
/// banker's rounding). Returns an f64.
fn round_to(x: f64, ndigits: i32) -> f64 {
    let factor = 10f64.powi(ndigits);
    (x * factor).round() / factor
}

/// `_compute_final_metrics(...)` — compute token counts, TPS, and build the final
/// metrics dict.
pub fn _compute_final_metrics(
    messages: &[Value],
    full_response: &str,
    total_duration: f64,
    time_to_first_token: Option<f64>,
    context_length: i64,
    real_input_tokens: i64,
    real_output_tokens: i64,
    has_real_usage: bool,
    tool_events: &[Value],
    round_texts: &[String],
    model: &str,
    last_round_input_tokens: i64,
    prep_timings: Option<&indexmap::IndexMap<String, f64>>,
    backend_gen_tps: f64,
    backend_prefill_tps: f64,
) -> Map<String, Value> {
    let (input_tokens, output_tokens) = if has_real_usage {
        (real_input_tokens, real_output_tokens)
    } else {
        let mut input_content = String::new();
        for msg in messages {
            if let Some(s) = msg.get("content").and_then(|c| c.as_str()) {
                input_content.push_str(s);
                input_content.push('\n');
            }
        }
        // len(...) // 4 over code points.
        (
            (input_content.chars().count() / 4) as i64,
            (full_response.chars().count() / 4) as i64,
        )
    };
    // Prefer the backend's true generation speed (llama.cpp
    // timings.predicted_per_second) — pure decode, no prefill/tool/network time.
    // Fall back to tokens/wall-clock only when the backend didn't report it
    // (e.g. cloud APIs without timings); that figure reads low because
    // total_duration includes prefill + agent overhead.
    let backend_gen = backend_gen_tps > 0.0;
    let tps = if backend_gen {
        backend_gen_tps
    } else if total_duration > 0.0 {
        output_tokens as f64 / total_duration
    } else {
        0.0
    };
    let ctx_tokens = if last_round_input_tokens > 0 {
        last_round_input_tokens
    } else {
        input_tokens
    };
    // ctx_pct = min(round((ctx/ctxlen)*100, 1), 100.0) if context_length else 0
    let ctx_pct: Value = if context_length != 0 {
        let pct = round_to((ctx_tokens as f64 / context_length as f64) * 100.0, 1).min(100.0);
        json!(pct)
    } else {
        json!(0)
    };

    let mut metrics = Map::new();
    metrics.insert("response_time".to_string(), json!(round_to(total_duration, 2)));
    metrics.insert(
        "time_to_first_token".to_string(),
        match time_to_first_token {
            Some(t) => json!(round_to(t, 2)),
            None => json!(0),
        },
    );
    metrics.insert("input_tokens".to_string(), json!(input_tokens));
    metrics.insert("output_tokens".to_string(), json!(output_tokens));
    metrics.insert("tokens_per_second".to_string(), json!(round_to(tps, 2)));
    // True decode speed when the backend reported it; "computed" = the
    // tokens/wall-clock fallback (reads low — includes prefill/overhead).
    metrics.insert(
        "tps_source".to_string(),
        json!(if backend_gen { "backend" } else { "computed" }),
    );
    metrics.insert("total_tokens".to_string(), json!(input_tokens + output_tokens));
    metrics.insert("context_length".to_string(), json!(context_length));
    metrics.insert("context_percent".to_string(), ctx_pct);
    metrics.insert(
        "usage_source".to_string(),
        json!(if has_real_usage { "real" } else { "estimated" }),
    );
    metrics.insert("model".to_string(), json!(model));

    if backend_prefill_tps > 0.0 {
        metrics.insert("prefill_tps".to_string(), json!(round_to(backend_prefill_tps, 2)));
    }
    if let Some(pt) = prep_timings {
        if !pt.is_empty() {
            let prep_total = round_to(pt.values().sum::<f64>(), 3);
            metrics.insert("agent_prep_time".to_string(), json!(prep_total));
            let wait = (time_to_first_token.unwrap_or(0.0) - prep_total).max(0.0);
            metrics.insert("agent_model_wait_time".to_string(), json!(round_to(wait, 3)));
            let mut breakdown = Map::new();
            for (k, v) in pt.iter() {
                breakdown.insert(k.clone(), json!(round_to(*v, 3)));
            }
            metrics.insert("agent_prep_breakdown".to_string(), Value::Object(breakdown));
        }
    }
    if !tool_events.is_empty() {
        metrics.insert("tool_events".to_string(), json!(tool_events));
        metrics.insert("round_texts".to_string(), json!(round_texts));
    }
    metrics
}

/// `_empty_response_fallback(full_response, round_reasoning, tool_events)` —
/// end-of-loop empty-response guard. Returns `(final_response, chunk)` where
/// `chunk` is the SSE string to yield, or `None` if nothing should be emitted.
///
/// When a thinking model routes all tokens to `reasoning_content` (leaving
/// content=""), `full_response` is empty but `round_reasoning` has content. The
/// reasoning was already streamed as `{thinking:true}` chunks — do not re-emit it
/// as a normal delta. Just persist it and yield nothing.
pub fn _empty_response_fallback(
    full_response: &str,
    round_reasoning: &str,
    tool_events: &[Value],
) -> (String, Option<String>) {
    if !full_response.trim().is_empty() || !tool_events.is_empty() {
        return (full_response.to_string(), None);
    }
    if !round_reasoning.trim().is_empty() {
        return (round_reasoning.to_string(), None);
    }
    let error_msg =
        "The model returned an empty response. Please try again or switch to a different model.";
    (
        error_msg.to_string(),
        Some(format!("data: {}\n\n", json!({"delta": error_msg}))),
    )
}

// ── Completion verifier ──

/// `_VERIFIER_EFFECTFUL_TOOLS` — tools whose effects produce a checkable artifact.
static _VERIFIER_EFFECTFUL_TOOLS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "create_document", "update_document", "edit_document",
        "bash", "python", "write_file",
    ]
    .into_iter()
    .collect()
});

const _VERIFIER_MAX_ROUNDS: i64 = 2;

/// `_build_actions_snapshot(tool_events, limit=8000)` — compact record of what the
/// agent did this turn, for the verifier to judge against.
pub fn _build_actions_snapshot(tool_events: &[Value], limit: usize) -> String {
    let mut parts: Vec<String> = Vec::new();
    for ev in tool_events {
        let tool = ev.get("tool").and_then(|t| t.as_str()).unwrap_or("?");
        let cmd = ev.get("command").and_then(|c| c.as_str()).unwrap_or("").trim().to_string();
        let out = ev.get("output").and_then(|o| o.as_str()).unwrap_or("").trim().to_string();
        let rc = ev.get("exit_code");
        let head = if cmd.is_empty() {
            format!("[{tool}]")
        } else {
            format!("[{tool}] {cmd}")
        };
        // rc_s = f" (exit {rc})" if rc not in (None, 0) else ""
        let rc_s = match rc {
            Some(Value::Number(n)) if n.as_i64() == Some(0) => String::new(),
            None | Some(Value::Null) => String::new(),
            Some(v) => format!(" (exit {v})"),
        };
        // body = (out[:1200] + " …") if len(out) > 1200 else (out or "(no output)")
        let body = if out.chars().count() > 1200 {
            let head_out: String = out.chars().take(1200).collect();
            format!("{head_out} …")
        } else if out.is_empty() {
            "(no output)".to_string()
        } else {
            out
        };
        parts.push(format!("{head}{rc_s}\n-> {body}"));
    }
    let snap = parts.join("\n\n");
    if snap.chars().count() > limit {
        snap.chars().take(limit).collect()
    } else {
        snap
    }
}

include!("agent_loop_web.rs");

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn detect_admin_intent_keys_on_last_user() {
        let msgs = vec![
            json!({"role": "user", "content": "rename my chats"}),
            json!({"role": "assistant", "content": "ok"}),
            json!({"role": "user", "content": "what is the weather"}),
        ];
        // last user message has no admin keyword
        assert!(!_detect_admin_intent(&msgs));
        let msgs2 = vec![json!({"role": "user", "content": "please delete that session"})];
        assert!(_detect_admin_intent(&msgs2));
    }

    #[test]
    fn extract_last_user_joins_blocks() {
        let msgs = vec![
            json!({"role": "user", "content": [{"type":"text","text":"hello"},{"type":"text","text":"world"}]}),
        ];
        assert_eq!(_extract_last_user_message(&msgs), "hello world");
    }

    #[test]
    fn recent_context_skips_tool_envelopes() {
        let msgs = vec![
            json!({"role": "user", "content": "first thing"}),
            json!({"role": "assistant", "content": "x"}),
            json!({"role": "user", "content": "[Tool execution results]\n\nstuff"}),
            json!({"role": "user", "content": "second thing"}),
        ];
        let q = _recent_context_for_retrieval(&msgs, 3, 600);
        assert!(q.contains("second thing"));
        assert!(q.contains("first thing"));
        assert!(!q.contains("Tool execution results"));
    }

    #[test]
    fn assemble_prompt_compact_lists_sorted_tools() {
        let mut names = HashSet::new();
        names.insert("bash".to_string());
        names.insert("python".to_string());
        let p = _assemble_prompt(&names, None, true);
        assert!(p.contains("Available tools: bash, python."));
    }

    #[test]
    fn settings_coercion_matches_python_truthiness_and_int() {
        // json_truthy == CPython bool(x).
        assert!(!json_truthy(&Value::Null));
        assert!(!json_truthy(&Value::Bool(false)));
        assert!(!json_truthy(&json!(0)));
        assert!(!json_truthy(&json!("")));
        assert!(!json_truthy(&json!([])));
        assert!(!json_truthy(&json!({})));
        assert!(json_truthy(&Value::Bool(true)));
        assert!(json_truthy(&json!(6000)));
        assert!(json_truthy(&json!("x")));
        // json_int_trunc == int(x): truncate floats, parse strings.
        assert_eq!(json_int_trunc(&json!(6000)), 6000);
        assert_eq!(json_int_trunc(&json!(6000.9)), 6000);
        assert_eq!(json_int_trunc(&json!("300")), 300);
        // `int(get_setting(...) or 0)`: a falsy value short-circuits to the
        // fallback (json_int_trunc is never reached), verified at the call sites.
    }

    #[test]
    fn pdf_form_doc_ctx_renders_form_mode() {
        // Pure formatter (no DB/settings): the FORM MODE block embeds the whole
        // markdown and the editing rules anchored on the `<!-- field=... -->`
        // comment, matching Python `_build_system_prompt` L641-674.
        let doc = ActiveDocument {
            id: "d1".to_string(),
            current_content: Some(
                "<!-- pdf_form_source upload_id=abc -->\n## Page 1\n\
- **Name:** value <!--field=name-->"
                    .to_string(),
            ),
            title: Some("Tax form".to_string()),
            language: None,
        };
        let ctx = build_pdf_form_doc_ctx(&doc);
        assert!(ctx.starts_with("ACTIVE PDF FORM (open in editor"));
        assert!(ctx.contains("Title: \"Tax form\""));
        assert!(ctx.contains("<!-- field=NAME type=TYPE -->"));
        assert!(ctx.contains("NEVER touch signature fields"));
        // The full markdown is embedded verbatim.
        assert!(ctx.contains("- **Name:** value <!--field=name-->"));
    }

    #[test]
    fn append_tool_results_null_content_when_only_tool_calls() {
        // gap 1: an assistant turn with empty content + tool_calls must carry
        // JSON null content (empty string breaks Ollama/Gemini), not "".
        let mut messages: Vec<Value> = Vec::new();
        let native = vec![json!({"id": "c1", "name": "bash", "arguments": "{}"})];
        _append_tool_results(&mut messages, "   ", &native, &[], &["ok".to_string()], true, 1, "");
        let assistant = &messages[0];
        assert_eq!(assistant.get("role").and_then(|r| r.as_str()), Some("assistant"));
        assert!(assistant.get("content").unwrap().is_null());
        // Non-empty prose is preserved verbatim.
        let mut messages2: Vec<Value> = Vec::new();
        _append_tool_results(&mut messages2, "hi", &native, &[], &["ok".to_string()], true, 1, "");
        assert_eq!(messages2[0].get("content").and_then(|c| c.as_str()), Some("hi"));
    }

    #[test]
    fn append_tool_results_strips_prior_reasoning_keeps_newest() {
        // gap 2: reasoning_content is stripped from PRIOR assistant turns; the
        // newest assistant turn (this one) keeps it.
        let mut messages: Vec<Value> = vec![
            json!({"role": "assistant", "content": "old", "reasoning_content": "stale think"}),
            json!({"role": "user", "content": "next"}),
        ];
        let native = vec![json!({"id": "c1", "name": "bash", "arguments": "{}"})];
        _append_tool_results(
            &mut messages, "now", &native, &[], &["ok".to_string()], true, 2, "fresh think",
        );
        // Prior assistant lost its reasoning_content.
        assert!(messages[0].get("reasoning_content").is_none());
        // The newest assistant turn (appended) keeps the fresh reasoning.
        let newest = messages.iter().rev().find(|m| {
            m.get("role").and_then(|r| r.as_str()) == Some("assistant")
        }).unwrap();
        assert_eq!(
            newest.get("reasoning_content").and_then(|v| v.as_str()),
            Some("fresh think")
        );
    }

    #[test]
    fn append_tool_results_replays_extra_content() {
        // gap 3: a native tool_call carrying extra_content (Gemini 3
        // thought_signature) must replay it on the assistant turn; calls
        // without it must NOT gain an extra_content key.
        let mut messages: Vec<Value> = Vec::new();
        let native = vec![
            json!({"id": "c1", "name": "bash", "arguments": "{}", "extra_content": {"thought_signature": "abc"}}),
            json!({"id": "c2", "name": "python", "arguments": "{}"}),
        ];
        _append_tool_results(
            &mut messages, "", &native, &[], &["a".to_string(), "b".to_string()], true, 1, "",
        );
        let calls = messages[0].get("tool_calls").and_then(|c| c.as_array()).unwrap();
        assert_eq!(
            calls[0].get("extra_content").and_then(|e| e.get("thought_signature")).and_then(|v| v.as_str()),
            Some("abc")
        );
        assert!(calls[1].get("extra_content").is_none());
    }

    #[test]
    fn compute_final_metrics_backend_tps_source() {
        // gap 6: when the backend reports gen_tps, tokens_per_second uses it and
        // tps_source is "backend"; prefill_tps is included when reported.
        let m = _compute_final_metrics(
            &[], "out", 10.0, Some(0.5), 0, 100, 200, true, &[], &[], "model-x", 0, None,
            42.0, 800.0,
        );
        assert_eq!(m.get("tokens_per_second").and_then(|v| v.as_f64()), Some(42.0));
        assert_eq!(m.get("tps_source").and_then(|v| v.as_str()), Some("backend"));
        assert_eq!(m.get("prefill_tps").and_then(|v| v.as_f64()), Some(800.0));

        // No backend gen_tps -> computed tokens/wall-clock, tps_source "computed",
        // and no prefill_tps key when backend_prefill_tps is 0.
        let m2 = _compute_final_metrics(
            &[], "out", 10.0, Some(0.5), 0, 100, 200, true, &[], &[], "model-x", 0, None,
            0.0, 0.0,
        );
        // output_tokens(200) / duration(10) = 20.0
        assert_eq!(m2.get("tokens_per_second").and_then(|v| v.as_f64()), Some(20.0));
        assert_eq!(m2.get("tps_source").and_then(|v| v.as_str()), Some("computed"));
        assert!(!m2.contains_key("prefill_tps"));
    }
}
