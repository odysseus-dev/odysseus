// src/tool_index.rs  <- src/tool_index.py
//! RAG-based tool selection for agent mode.
//!
//! Instead of injecting all tool descriptions into the system prompt, embed them
//! in a (ChromaDB-shaped, SQLite-backed) collection and retrieve only the top-K
//! relevant ones per user message.
//!
//! ## Port classification — PORT_VIA_DB (web)
//!
//! `ToolIndex` is the live RAG port over `crate::src::chroma_client` (the deliberate
//! ChromaDB->SQLite swap facade) and a shared `Arc<EmbeddingClient>`. This module is
//! the **canonical home** for `ALWAYS_AVAILABLE`, `ASSISTANT_ALWAYS_AVAILABLE`,
//! `COLLECTION_NAME`, `BUILTIN_TOOL_DESCRIPTIONS`, `_KEYWORD_HINTS`, `_SCHEDULE_RE`,
//! and `get_tools_for_query`.
//!
//! ### Faithful behaviours / documented decisions
//!
//! * **`np` branch dropped.** `EmbeddingClient::encode(texts, normalize_embeddings=true)`
//!   already returns normalized `Vec<Vec<f32>>`, so the Python `np.array(...).tolist()`
//!   vs `[list(v) for v in vecs]` fork collapses to a single async path. There is no
//!   numpy to be missing.
//! * **MCP generation = 0 (constant).** `McpManager` has **no** `_generation` field
//!   (verified absent in both Python and Rust). The Python reads it via
//!   `getattr(mcp_mgr, '_generation', 0)`, so the value is always `0`. With
//!   `_mcp_generation` starting at `-1`, the first `index_mcp_tools` reindexes and
//!   every subsequent call short-circuits (`0 == 0`). This is the faithful behaviour
//!   ("reindex once, then permanent no-op"), **not** an invented counter.
//! * **`.add()` is upsert.** `VectorCollection::add` is `INSERT OR REPLACE`, exactly
//!   ChromaDB's `collection.upsert`. The Python `upsert(...)` calls map to `.add(...)`.
//! * **30s singleton throttle** via `std::time::Instant` (Python `time.monotonic()`),
//!   `RETRY_INTERVAL = 30`.
//!
//! Feature gate: `web` (HTTP embeddings + the rusqlite-backed vector store).

use std::collections::HashSet;
use std::sync::Arc;
use std::time::Instant;

use indexmap::IndexMap;
use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tokio::sync::Mutex;

use crate::error::{PyError, PyResult};
use crate::pylog as logger;
use crate::src::chroma_client::get_chroma_client;
use crate::src::embeddings::{get_embedding_client, EmbeddingClient};
use crate::src::mcp_manager::McpManager;
use crate::src::vector_store::VectorCollection;

// ---------------------------------------------------------------------------
// Module-level constants
// ---------------------------------------------------------------------------

/// `ALWAYS_AVAILABLE` — tools that are ALWAYS included regardless of retrieval
/// results. These are the most commonly needed and should never be missing.
///
/// (`bash`/`python`/`web_search`/`web_fetch`/`read_file`, `api_call` for configured
/// integrations, the two ambient cookbook tools `list_served_models`/
/// `stop_served_model`, and the generic `app_api` loopback.)
pub static ALWAYS_AVAILABLE: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "bash",
        "python",
        "web_search",
        "web_fetch",
        "read_file",
        "api_call",
        "list_served_models",
        "stop_served_model",
        "app_api",
    ]
    .into_iter()
    .collect()
});

/// `ASSISTANT_ALWAYS_AVAILABLE` — tools the Personal Assistant always has access to
/// during scheduled check-ins and proactive tasks, in addition to RAG-selected
/// tools.
pub static ASSISTANT_ALWAYS_AVAILABLE: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    [
        "list_email_accounts",
        "list_emails",
        "read_email",
        "send_email",
        "reply_to_email",
        "bulk_email",
        "archive_email",
        "delete_email",
        "mark_email_read",
        "manage_calendar",
        "manage_notes",
        "manage_tasks",
        "manage_memory",
        "web_search",
        "read_file",
        "create_document",
        "update_document",
        "resolve_contact",
        "search_chats",
        "api_call",
        "ui_control",
    ]
    .into_iter()
    .collect()
});

/// `COLLECTION_NAME`.
pub const COLLECTION_NAME: &str = "odysseus_tool_index";

/// `BUILTIN_TOOL_DESCRIPTIONS` — the tool description registry.
///
/// Each tool gets a searchable description that helps retrieval. These are richer
/// than the system prompt one-liners — they are for embedding. An `IndexMap`
/// preserves the Python dict insertion order (the order in which builtin docs are
/// embedded / the fingerprint is computed over the *sorted* keys, but iteration
/// order is still mirrored for review parity). The `\Seen` escape in
/// `mark_email_read` is preserved verbatim.
pub static BUILTIN_TOOL_DESCRIPTIONS: Lazy<IndexMap<&'static str, &'static str>> = Lazy::new(|| {
    let mut m = IndexMap::new();
    m.insert("bash", "Run shell commands on the server. Install packages, check files, git operations, curl, system info, process management, networking.");
    m.insert("python", "Execute Python code for computation, data processing, math, scripting, parsing, API calls. Not for writing code for the user.");
    m.insert("web_search", "Quick single web lookup for a fact, current event, or doc mid-task. NOT for 'research X' / 'do research on X' requests — those are deep-research jobs (use trigger_research). web_search = one query; trigger_research = a full researched report in the sidebar.");
    m.insert("web_fetch", "Fetch and read the text content of a specific URL/website the user names (e.g. 'check example.com', 'open this link'). Use when you have a concrete URL; for open-ended lookups use web_search instead.");
    m.insert("read_file", "Read a file from disk and return its contents. View source code, config files, logs.");
    m.insert("write_file", "Write content to a file on disk. Create new files, save output, update configs.");
    m.insert("create_document", "Create a new document in the editor panel. For code, articles, text content longer than 15 lines, unless an already-open document/email draft is the obvious target. If an email compose draft is open, edit that draft instead of creating another document.");
    m.insert("edit_document", "Preferred tool for editing an existing document — targeted find-and-replace. Use for any small change: add a function, fix a bug, tweak a section, rename things.");
    m.insert("update_document", "Replace the entire active document content. ONLY for full rewrites (>50% changed). Do not use for small edits — use edit_document instead.");
    m.insert("suggest_document", "Suggest changes to the active document with explanations. For code review, proofreading, feedback requests.");
    m.insert("generate_image", "Generate an AI image from a text prompt. Specify model, size, and quality. Art, illustrations, photos.");
    m.insert("chat_with_model", "Send a message to a different AI model. Compare responses, get specialized help, delegate tasks.");
    m.insert("ask_teacher", "Ask a more capable model for help with a difficult problem. Escalate complex tasks.");
    m.insert("pipeline", "Run a multi-step AI pipeline with multiple models. Chain tasks together in sequence.");
    m.insert("list_models", "List all available AI models and their endpoints.");
    m.insert("manage_session", "Chat management: rename, archive, delete, or fork chats (the UI calls these 'chats'; internally 'sessions'). Use for 'rename my chats', 'rename this chat', 'archive/delete a chat'.");
    m.insert("manage_memory", "Memory management: list, add, edit, delete, or search persistent memories.");
    m.insert("manage_skills", "Skill management: add, update, publish, or search reusable skills/presets.");
    m.insert("manage_tasks", "Scheduled task management: list, create, edit, delete, pause, resume, or run cron tasks.");
    m.insert("manage_endpoints", "Endpoint management: list, add, delete, enable, or disable model API endpoints.");
    m.insert("manage_mcp", "MCP server management: list, add, delete, reconnect servers, or list available tools.");
    m.insert("manage_webhooks", "Webhook management: list, add, delete, enable, or disable webhooks.");
    m.insert("manage_tokens", "API token management: list, create, or delete API access tokens.");
    m.insert("manage_documents", "List, read, delete, or tidy documents in the editor panel. action='list' returns clickable rows (most-recent first) so the user can open any doc by clicking. action='read' (aka view/open/get) with document_id returns the content. action='delete' with document_id removes a doc (only way to delete). Use this for ANY 'show/read/list/open my documents/docs/files/notes' request — never shell or curl.");
    m.insert("manage_research", "List, read/open, or delete saved DEEP RESEARCH results from the Library. action='list' returns clickable [query](#research-<id>) rows (most-recent first). action='read' (aka open/view/get) with id returns the report + sources. action='delete' with id removes it. Use this for ANY 'open/read/find/delete my research / that report / the research on X' request. NOTE: this is for EXISTING research; to START new research use trigger_research.");
    m.insert("manage_settings", "Change ANY real app setting (the ones the Settings panel writes) so the user never has to open it: TTS voice/provider/speed, STT, search engine + result count, default/teacher/task/utility/vision/image/research models, image quality, reminder channel (browser/email/ntfy), agent timeout/tool-call budget, and more. action=set with key (friendly aliases ok: voice, 'search engine', 'default model', 'teacher model', 'image quality', 'reminder channel'...) + value; get/list/reset too. Also toggles tools on/off (disable_tool/enable_tool/list_tools). Secrets/API keys are read-only. Use for any 'change my…/set my…/use X for…/turn on…' preference request.");
    m.insert("create_session", "Create a new chat with a name and model.");
    m.insert("list_sessions", "List all chats with their metadata (the UI calls these 'chats'). Use for 'list my chats', 'rename all my chats' (list first, then manage_session to rename each).");
    m.insert("send_to_session", "Send a message to another chat. Cross-chat communication.");
    m.insert("search_chats", "Search through chat history across all sessions.");
    m.insert("ui_control", "Control the UI and toggle tools on/off. Use this to turn off / turn on / disable / enable individual tools and features: shell (bash), search (web), research, browser, documents, incognito. Open panels (documents library, gallery, email inbox, sessions, notes, memories/brain, skills, settings, cookbook) via `open_panel <name>`. Use `open_email_reply <uid> <folder> reply` to open an email reply draft document without sending. Also switches between chat/agent modes, changes the current model, and applies/creates themes.");
    m.insert("list_email_accounts", "List configured email accounts and default status. Use before reading or sending mail when the user mentions Gmail, work mail, custom domain mail, another mailbox, or asks to compare/check multiple inboxes.");
    m.insert("list_emails", "List emails for a folder/account, newest first, including read messages by default. Shows subject, sender, date, UID, account, and AI summary. Check inbox, find emails needing replies. Supports account from list_email_accounts for Gmail/work/custom mailboxes. For last/latest/newest email, use max_results=1 and unread_only=false.");
    m.insert("read_email", "Read the full content of a specific email by UID or Message-ID. View email body, check details. Supports account from list_email_accounts when the UID belongs to a non-default mailbox.");
    m.insert("send_email", "Send a new email via SMTP. Provide recipient, subject, body, and optional account from list_email_accounts. For replying to a thread use reply_to_email instead.");
    m.insert("reply_to_email", "SEND a reply email immediately by UID. Do not use for open/start reply draft requests; use ui_control open_email_reply for those. For follow-up 'reply ...' send requests, use the exact UID and account from latest read_email/list_emails output; never invent UID 1. Threads automatically with In-Reply-To/References, prefixes Re:, marks original as Answered.");
    m.insert("archive_email", "Move an email out of the inbox into the Archive folder. Use after handling messages you want to keep but get out of the way.");
    m.insert("delete_email", "Delete an email — moves to Trash by default, or expunges permanently with permanent=true.");
    m.insert("mark_email_read", "Mark an email as read or unread by toggling the \\Seen flag.");
    m.insert("bulk_email", "Perform one action on many emails at once. Use for delete all those, archive these, mark all read, move spam to junk. Takes explicit UIDs from list_emails or all_unread=true. Always pass account for Gmail/work/custom mailbox results.");
    m.insert("resolve_contact", "Look up a contact's email address by name. Searches CardDAV address book and sent email history. Use when the user says 'message [name]', 'email [name]', or 'send to [name]' without an email address.");
    m.insert("manage_contact", "Create, update, delete, or list CardDAV contacts. Use to save a new contact, change an existing one's email/phone, or remove one. Action=list returns uids needed for update/delete. Use when the user says 'save this contact', 'add [name] to contacts', 'update [name]'s email', 'delete [name] from contacts'. Do not use for user identity facts like 'my name is <name>'; those are memory.");
    m.insert("manage_notes", "Create and manage notes and checklists (Google Keep-style). ALWAYS use this for note/todo/checklist/reminder creation — NEVER hit /api/notes via app_api. Accepts natural-language `due_date` like 'tomorrow at 9am' or '11pm today' (parsed in the USER'S timezone). The due_date IS the reminder — it fires a notification at that time, so do NOT also create a calendar event for the same reminder. Set colors, labels, pin, archive. Do NOT use manage_memory for note content.");
    m.insert("manage_calendar", "Calendar event management: list, create, update, delete. Each event can carry a tag/category (event_type — work/personal/health/travel/meal/social/admin/other) and importance (low/normal/high/critical). Use ISO datetimes; supports all-day events. For event reminders/alarms, pass reminder_minutes; this creates the Notes reminder, so do not also call manage_notes for the same reminder.");
    m.insert("download_model", "Download a HuggingFace model to a local or remote server. Specify repo_id (e.g. 'Qwen/Qwen3-8B'), optional server host, and optional include filter for specific files.");
    m.insert("serve_model", "Start serving a model with vLLM, SGLang, llama.cpp, Ollama, or Diffusers. For image/inpainting/diffusion use python3 scripts/diffusion_server.py --model <repo> --port 8100. After launch, call list_served_models for readiness/errors and retry suggestions.");
    m.insert("list_served_models", "List currently running model servers in the Cookbook — shows status (loading, ready, idle, error), model name, port, throughput, and serve failure diagnosis/retry suggestions. Use when the user asks 'what's running', 'show my cookbook', 'which models are up', 'what's serving'.");
    m.insert("stop_served_model", "Stop a running model server in the Cookbook by session ID or model name. Use when the user says 'kill my cookbook', 'stop the model', 'kill the serve', 'shut down vLLM', 'cancel the running model'.");
    m.insert("list_downloads", "List in-progress HuggingFace model downloads in the Cookbook. Shows model name, phase, percent, session ID. Use for 'what's downloading', 'show my downloads', 'check download progress'.");
    m.insert("cancel_download", "Cancel an in-progress model download by tmux session ID. Use for 'cancel the download', 'stop downloading X', 'kill the download'. Call list_downloads first to get the session_id.");
    m.insert("search_hf_models", "Search HuggingFace for models matching a query (e.g. 'qwen 8B', 'flux', 'llama-3 instruct'). Returns ranked repo IDs with sizes and download counts. Use for 'find a model', 'search huggingface for X', 'what models are there for Y'.");
    m.insert("list_cached_models", "List models already cached on disk locally or on a remote host. Accepts friendly Cookbook server names like ajax. Use for 'what models do I have', 'show cached models', 'is X downloaded', 'list my models'. Avoids re-downloading.");
    m.insert("list_serve_presets", "List saved Cookbook serve presets (templates with model+host+port+cmd). Always call this BEFORE serve_model when the user asks to launch a known model — they probably have a preset for it from the UI.");
    m.insert("serve_preset", "Launch a saved Cookbook serve preset by name. Reuses the exact tmux command + host the user already saved. Use for 'run stable diffusion 3.5', 'serve vllm-qwen', 'start the inpaint model' — preset-name matches the user's UI labels.");
    m.insert("adopt_served_model", "Register an existing tmux model server (one started manually or outside the cookbook flow) into Cookbook tracking AND add it as a chat endpoint. Use when the user (or a previous turn) launched something via ssh+tmux and now wants it visible in the UI, stoppable via stop_served_model, and usable in the model picker.");
    m.insert("list_cookbook_servers", "List the cookbook's configured servers (remote GPU boxes + local) and which is the current default. Use this BEFORE download_model/serve_model when the user didn't name a host — to decide where to run, or to ask the user which server when ambiguous. Downloads/serves default to the cookbook's selected server, NOT localhost.");
    m.insert("app_api", "Generic loopback to ANY Odysseus internal endpoint. Use this when the user wants something the UI can do but there's no named tool for it. Covers calendar, gallery, library/documents, memory, notes, tasks, settings, research, compare, cookbook GPUs/state — every UI button hits some /api/* endpoint and you can hit it too. action='endpoints' with filter=<keyword> lists available endpoints. action='call' takes method+path+body. Hits same routes the UI uses — auth flows free. NOTE: themes are NOT an API endpoint — use the ui_control tool (create_theme / set_theme), not app_api. SESSIONS/CHATS: do NOT use app_api for these — GET /api/sessions returns EMPTY for tool calls (it's owner-filtered and tool calls authenticate as a different identity). EMAIL ACCOUNTS: do NOT use /api/email/accounts via app_api; use list_email_accounts, list_emails, and read_email instead. To list/rename/archive/delete/fork chats use the list_sessions and manage_session tools instead.");
    m.insert("edit_image", "Edit an image in the gallery: upscale (increase resolution), remove background (rembg), inpaint (fill selected area), or harmonize (blend edits). Specify image ID and action.");
    m.insert("trigger_research", "Start a deep research job on any topic — appears in the Deep Research sidebar, streams progress, produces a detailed report. Use for 'research X', 'look into Y', 'do deep research on Z', 'investigate'. NOT a scheduled task — it runs now and surfaces in the sidebar.");
    m
});

/// `ToolIndex._SCHEDULE_RE` — structural recurring-schedule intent.
///
/// Typo-resilient (matches "every dya" via "every <word>"), and catches bare clock
/// times ("at 7:30 am", "7am"). Used in addition to the literal keyword hints. The
/// `(?i)` inline flag mirrors `re.I`; the pattern uses no lookaround, so the plain
/// `regex` crate compiles it directly.
static SCHEDULE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(concat!(
        r"(?i)",
        r"\bevery\s+\w+",
        r"|\b(?:daily|nightly|hourly|weekly|monthly)\b",
        r"|\beach\s+(?:day|morning|night|week|hour|evening)\b",
        r"|\bat\s+\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\b",
    ))
    .expect("tool_index _SCHEDULE_RE must compile")
});

/// `ToolIndex._KEYWORD_HINTS` — `(keywords, tools)` pairs: if the query mentions one
/// of the keywords, force-include the tools.
///
/// A `Vec` preserves the source order (matching is order-independent, but the Vec
/// keeps the literal source layout for review). The keyword sets and tool sets are
/// Python `frozenset`s; here they are `Vec<&str>` because membership is accumulated
/// into a result set, so the set semantics of the originals are not load-bearing for
/// iteration. Matching is WORD-BOUNDARY based (`\b{kw}\b`, see [`KEYWORD_HINT_RES`]),
/// not raw substring, so short hints like "fix", "line", "serve", "reply" or "unread"
/// don't fire inside unrelated words ("prefix", "deadline"/"online",
/// "observe"/"reserve", "replying", "unreadable").
static KEYWORD_HINTS: Lazy<Vec<(Vec<&'static str>, Vec<&'static str>)>> = Lazy::new(|| {
    vec![
        // NOTE: "tell" was removed from this set. It fired on any "tell me ..."
        // request (e.g. "visit <url> and tell me the title"), force-including the
        // whole email toolset and crowding out the relevant tools — the model then
        // believed it had only email tools and refused web/other tasks (#1707).
        (
            vec!["email", "mail", "gmail", "googlemail", "message", "send", "reply", "inbox", "unread"],
            vec!["list_email_accounts", "list_emails", "read_email", "send_email", "reply_to_email", "bulk_email", "delete_email", "archive_email", "mark_email_read", "resolve_contact", "ui_control"],
        ),
        (
            vec!["calendar", "event", "meeting", "schedule", "appointment"],
            vec!["manage_calendar"],
        ),
        (
            vec!["note", "todo", "reminder", "remind", "checklist", "remember to"],
            vec!["manage_notes"],
        ),
        (
            vec!["sessions", "my chats", "these chats", "those chats", "chat history", "rename chat", "rename session", "rename the chat", "rename my chat", "rename the session", "archive chat", "archive session", "delete chat", "delete session", "fork chat", "fork session", "name the chats", "name my chats", "rename them"],
            vec!["list_sessions", "manage_session"],
        ),
        (
            vec!["recurring", "every day", "every hour", "every morning", "every evening", "every night", "every week", "each morning", "daily task", "background task", "scheduled task", "schedule a", "automatically", "auto-summarize", "auto summarize", "cron", "periodically", "on a schedule", "set up a task", "create a task", "summarize my inbox every", "remind me every"],
            vec!["manage_tasks"],
        ),
        (
            vec!["contact", "address", "phone", "who is"],
            vec!["resolve_contact", "manage_contact"],
        ),
        (
            vec!["save contact", "add contact", "new contact", "update contact", "edit contact", "delete contact", "remove contact", "save this person", "add to contacts", "save to contacts"],
            vec!["manage_contact"],
        ),
        (
            vec!["ask gpt", "ask claude", "ask gemini", "ask deepseek", "ask minimax", "ask qwen", "ask the", "ask another model", "what does", "what would", "second opinion", "other model", "different model", "compare answers", "compare models", "delegate to", "have model"],
            vec!["chat_with_model", "ask_teacher", "list_models"],
        ),
        (
            vec!["research", "reserach", "reasearch", "look into", "investigate", "deep dive", "deep research", "find out about", "study up on", "report on", "do research", "look up everything"],
            vec!["trigger_research"],
        ),
        (
            vec!["change my", "set my", "use the voice", "change the voice", "my voice", "tts voice", "search engine", "default model", "teacher model", "task model", "background model", "image quality", "reminder channel", "send reminders to", "remind me by", "speak faster", "speak slower", "agent timeout", "token budget", "max tool calls", "use this model for", "use that model for", "my settings", "change setting", "change a setting", "set setting", "preference", "preferences", "configure"],
            vec!["manage_settings", "ui_control"],
        ),
        (
            vec!["my research", "the research", "research on", "open research", "read research", "find research", "delete research", "remove research", "list research", "my reports", "the report", "saved research", "research library", "past research", "research i did", "research about"],
            vec!["manage_research", "trigger_research"],
        ),
        (
            vec!["edit", "change", "fix", "rewrite", "update", "replace", "add a", "tweak", "modify", "rename", "paragraph", "section", "line", "the doc", "the document", "in the doc"],
            vec!["edit_document", "update_document", "create_document", "suggest_document"],
        ),
        (
            vec!["delete this doc", "delete the doc", "delete document", "remove document", "remove the doc", "trash", "list documents", "list docs", "all my docs", "my documents", "my docs", "my files", "open the", "open my", "open document", "open doc", "find the", "find my", "find document", "read the", "read my", "show me the", "show my", "the file", "my file", "the report", "the write-up", "the writeup", "saved document", "in my library", "in the library"],
            vec!["manage_documents", "edit_document"],
        ),
        (
            vec!["theme", "color scheme", "colors of the ui", "make it dark", "make it light", "make the ui", "switch theme", "change theme", "dark mode", "light mode", "toggle"],
            vec!["ui_control"],
        ),
        (
            vec!["cookbook", "kill cookbook", "stop cookbook", "stop the model", "kill the model", "kill my model", "what's running", "what is running", "whats running", "running models", "running model", "running server", "shut down vllm", "shutdown vllm", "stop vllm", "stop serving", "kill serve", "cancel serve"],
            vec!["list_served_models", "stop_served_model"],
        ),
        (
            vec!["serve", "launch", "spin up", "start the model", "run the model", "preset", "presets", "which server", "what servers", "gpu box", "cookbook server", "vllm", "on the server", "on the gpu"],
            vec!["serve_preset", "serve_model", "list_serve_presets", "list_cookbook_servers", "list_cached_models"],
        ),
        (
            vec!["download", "downloading", "downloads", "cancel download", "stop download", "kill download", "what's downloading", "download progress", "pull model", "grab model"],
            vec!["list_downloads", "cancel_download", "download_model", "list_cookbook_servers"],
        ),
        (
            vec!["huggingface", "hugging face", "hf search", "find a model", "search models", "search for a model", "models for", "best model for"],
            vec!["search_hf_models", "list_cached_models"],
        ),
        (
            vec!["cached models", "list models", "my models", "what models do i have", "is it downloaded", "do i have", "already downloaded", "on disk"],
            vec!["list_cached_models", "search_hf_models"],
        ),
        (
            vec!["turn off", "turn on", "disable", "enable", "shell off", "shell on", "search off", "search on", "research off", "research on", "incognito", "switch model", "change model", "set mode", "agent mode", "chat mode", "open library", "open documents", "open gallery", "open email", "open inbox", "open settings", "open memories", "open memory", "open skills", "open notes", "open chats", "open sessions", "show library", "show gallery", "show inbox", "show settings", "show memory", "show memories", "show skills", "show notes", "show chats", "show sessions", "show documents"],
            vec!["ui_control"],
        ),
        (
            vec!["write a", "create a doc", "draft", "compose", "poem", "story", "essay", "outline", "letter"],
            vec!["create_document", "edit_document", "update_document"],
        ),
    ]
});

/// Compiled word-boundary matchers for [`KEYWORD_HINTS`].
///
/// The Python tests each hint with `re.search(rf"\b{re.escape(kw)}\b", ql)`. We
/// precompile one `\b{escaped}\b` regex per keyword (case-insensitive — the query is
/// already lowercased before matching, so `(?i)` is belt-and-suspenders and matches
/// Python's behaviour of searching the lowercased `ql`). The outer `Vec` mirrors
/// `KEYWORD_HINTS` index-for-index; each entry pairs the compiled keyword matchers
/// with that bucket's tool list.
///
/// `regex::escape` is the analogue of Python's `re.escape`, and `\b` is a zero-width
/// word boundary in the `regex` crate just as in Python's `re`.
#[allow(clippy::type_complexity)]
static KEYWORD_HINT_RES: Lazy<Vec<(Vec<Regex>, &'static Vec<&'static str>)>> = Lazy::new(|| {
    KEYWORD_HINTS
        .iter()
        .map(|(keywords, tools)| {
            let res: Vec<Regex> = keywords
                .iter()
                .map(|kw| {
                    Regex::new(&format!(r"(?i)\b{}\b", regex::escape(kw)))
                        .expect("tool_index keyword-hint regex must compile")
                })
                .collect();
            (res, tools)
        })
        .collect()
});

// ---------------------------------------------------------------------------
// ToolIndex
// ---------------------------------------------------------------------------

/// `ToolIndex` — ChromaDB-backed (SQLite-backed in the Rust port) tool index for
/// RAG-based tool selection.
pub struct ToolIndex {
    /// `self._embedder` — the shared embedding client. `Arc` so the singleton can
    /// hold it cheaply (the Python keeps one `get_embedding_client()` handle).
    embedder: Arc<EmbeddingClient>,
    /// `self._collection` — the `odysseus_tool_index` collection.
    collection: VectorCollection,
    /// `self._fingerprint` — sha256 over the sorted builtin keys.
    fingerprint: String,
    /// `self._mcp_generation` — last-seen MCP generation. Starts at `-1` so the
    /// first `index_mcp_tools` always reindexes (then `0 == 0` no-ops forever, since
    /// `McpManager` has no `_generation` field — see module docs).
    mcp_generation: i64,
    /// `self._healthy`.
    healthy: bool,
}

impl ToolIndex {
    /// `ToolIndex.__init__`.
    ///
    /// Async because `get_embedding_client()` probes the embedding endpoint over
    /// HTTP. Raises (`Err`) if no embedding client is available — the Python
    /// `RuntimeError("No embedding client available")`.
    pub async fn new() -> PyResult<ToolIndex> {
        // self._embedder = get_embedding_client()
        // if not self._embedder: raise RuntimeError("No embedding client available")
        let embedder = match get_embedding_client().await {
            Some(c) => Arc::new(c),
            // raise RuntimeError("No embedding client available")
            // (the project maps RuntimeError -> PyError::other; see rag_vector.)
            None => return Err(PyError::other("No embedding client available")),
        };

        // client = get_chroma_client()
        // self._collection = client.get_or_create_collection(name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"})
        let client = get_chroma_client();
        let collection = client.get_or_create_collection(COLLECTION_NAME)?;

        // self._fingerprint = ""; self._mcp_generation = -1; self._healthy = True
        logger::info("ToolIndex initialized");
        Ok(ToolIndex {
            embedder,
            collection,
            fingerprint: String::new(),
            mcp_generation: -1,
            healthy: true,
        })
    }

    /// `@property healthy`.
    pub fn healthy(&self) -> bool {
        self.healthy
    }

    /// `_embed(texts)` — embed texts via the shared client.
    ///
    /// `EmbeddingClient::encode(texts, normalize_embeddings=true)` already returns
    /// normalized `Vec<Vec<f32>>`, so the Python numpy-vs-list fork collapses to one
    /// path (there is no numpy to be missing).
    async fn embed(&self, texts: &[String]) -> PyResult<Vec<Vec<f32>>> {
        self.embedder.encode(texts, true).await
    }

    /// `index_builtin_tools()` — index all built-in tool descriptions.
    pub async fn index_builtin_tools(&mut self) -> PyResult<()> {
        let mut docs: Vec<String> = Vec::new();
        let mut ids: Vec<String> = Vec::new();
        let mut metadatas: Vec<Value> = Vec::new();
        for (name, desc) in BUILTIN_TOOL_DESCRIPTIONS.iter() {
            // doc_text = f"Tool: {name}\n{desc}"
            docs.push(format!("Tool: {name}\n{desc}"));
            ids.push(format!("builtin_{name}"));
            metadatas.push(json!({"tool_name": name, "tool_type": "builtin"}));
        }

        // if not docs: return
        if docs.is_empty() {
            return Ok(());
        }

        // Drop any stale builtin_* entries that aren't in the current registry.
        // Without this, upsert leaves them in place and RAG keeps surfacing tools
        // that no longer exist. Best-effort (logged at debug on failure).
        let id_set: HashSet<&String> = ids.iter().collect();
        match self.collection.get_where(Some(&json!({"tool_type": "builtin"}))) {
            Ok(existing) => {
                let stale: Vec<String> = existing
                    .ids
                    .into_iter()
                    .filter(|i| !id_set.contains(i))
                    .collect();
                if !stale.is_empty() {
                    if let Err(e) = self.collection.delete(&stale) {
                        logger::debug(&format!("Stale-pruning skipped: {e}"));
                    } else {
                        logger::info(&format!(
                            "Pruned {} stale builtin tool entries from index",
                            stale.len()
                        ));
                    }
                }
            }
            Err(e) => logger::debug(&format!("Stale-pruning skipped: {e}")),
        }

        // embeddings = self._embed(docs)
        // self._collection.upsert(ids=, documents=, embeddings=, metadatas=)
        let embeddings = self.embed(&docs).await?;
        self.collection.add(&ids, &embeddings, &docs, &metadatas)?;

        // self._fingerprint = sha256(",".join(sorted(BUILTIN_TOOL_DESCRIPTIONS.keys())))
        let mut keys: Vec<&str> = BUILTIN_TOOL_DESCRIPTIONS.keys().copied().collect();
        keys.sort_unstable();
        let joined = keys.join(",");
        let mut hasher = Sha256::new();
        hasher.update(joined.as_bytes());
        self.fingerprint = hex::encode(hasher.finalize());

        logger::info(&format!("Indexed {} built-in tools", docs.len()));
        Ok(())
    }

    /// `index_mcp_tools(mcp_mgr, disabled_map=None)` — index MCP tool descriptions.
    /// Call after MCP servers connect/disconnect.
    ///
    /// The Python reads `getattr(mcp_mgr, '_generation', 0)`; `McpManager` has no
    /// such field, so the generation is the constant `0`. The first call reindexes
    /// (`-1 != 0`), every subsequent call short-circuits (`0 == 0`).
    pub async fn index_mcp_tools(
        &mut self,
        mcp_mgr: Option<&McpManager>,
        disabled_map: Option<&std::collections::HashMap<String, HashSet<String>>>,
    ) -> PyResult<()> {
        // if not mcp_mgr: return
        let mcp_mgr = match mcp_mgr {
            Some(m) => m,
            None => return Ok(()),
        };

        // gen = getattr(mcp_mgr, '_generation', 0)  # always 0 (field absent)
        // if gen == self._mcp_generation: return
        let gen: i64 = 0;
        if gen == self.mcp_generation {
            return Ok(());
        }
        self.mcp_generation = gen;

        // Remove old MCP entries (best-effort, errors swallowed like Python's
        // `except Exception: pass`).
        if let Ok(existing) = self.collection.get_where(Some(&json!({"tool_type": "mcp"}))) {
            if !existing.ids.is_empty() {
                let _ = self.collection.delete(&existing.ids);
            }
        }

        // all_tools = mcp_mgr.get_tool_descriptions_for_prompt(disabled_map or {})
        // (sync; cannot raise in the Rust port — the `except Exception: all_tools = ""`
        // guard has no failing call to catch.)
        let all_tools = mcp_mgr.get_tool_descriptions_for_prompt(disabled_map);

        // if not all_tools: return
        if all_tools.is_empty() {
            return Ok(());
        }

        // Parse MCP tool descriptions from the prompt text.
        let mut docs: Vec<String> = Vec::new();
        let mut ids: Vec<String> = Vec::new();
        let mut metadatas: Vec<Value> = Vec::new();
        let mut current_server = String::new();
        for raw_line in all_tools.trim().split('\n') {
            let line = raw_line.trim();
            // Track which server section we're in (for context in descriptions).
            if line.starts_with("**") && line.ends_with(":**") {
                // line.strip("*: ") — strip leading/trailing '*', ':' and ' '.
                current_server = line
                    .trim_matches(|c| c == '*' || c == ':' || c == ' ')
                    .to_string();
            } else if line.starts_with("- ") && line.contains(':') {
                // Format: "- tool_name: description"
                let body = &line[2..];
                // name_desc = line[2:].split(":", 1)
                if let Some((name_part, desc_part)) = body.split_once(':') {
                    let name = name_part.trim();
                    let desc = desc_part.trim();
                    let server_ctx = if !current_server.is_empty() {
                        format!(" (server: {current_server})")
                    } else {
                        String::new()
                    };
                    docs.push(format!("Tool: {name}{server_ctx}\n{desc}"));
                    ids.push(format!("mcp_{name}"));
                    metadatas.push(json!({"tool_name": name, "tool_type": "mcp"}));
                }
            }
        }

        // if not docs: return
        if docs.is_empty() {
            return Ok(());
        }

        let embeddings = self.embed(&docs).await?;
        self.collection.add(&ids, &embeddings, &docs, &metadatas)?;
        logger::info(&format!("Indexed {} MCP tools", docs.len()));
        Ok(())
    }

    /// `retrieve(query, k=8)` — retrieve the top-K most relevant tool names for a
    /// query. First-seen-order dedupe; any failure logs a warning and returns `[]`.
    pub async fn retrieve(&self, query: &str, k: i64) -> Vec<String> {
        match self.retrieve_inner(query, k).await {
            Ok(names) => names,
            Err(e) => {
                logger::warning(&format!("Tool retrieval failed: {e}"));
                Vec::new()
            }
        }
    }

    /// Fallible body of `retrieve` so the Python `try/except` maps to `Result`.
    async fn retrieve_inner(&self, query: &str, k: i64) -> PyResult<Vec<String>> {
        // query_embedding = self._embed([query])
        let query_embedding = self.embed(&[query.to_string()]).await?;
        let query_vec: &[f32] = match query_embedding.first() {
            Some(v) => v,
            // The embedding client returns one vector per input; an empty result
            // means nothing to query against.
            None => return Ok(Vec::new()),
        };

        // n_results=min(k, self._collection.count() or k)
        let count = self.collection.count()?;
        let effective = if count == 0 { k } else { count };
        let n_results = std::cmp::min(k, effective);

        // results = self._collection.query(query_embeddings=, n_results=, include=["metadatas","distances"])
        let results = self.collection.query(query_vec, n_results, None)?;

        // if not results or not results.get("metadatas"): return []
        // (QueryResult.metadatas is `[[...]]`; an empty inner list means no hits.)
        let mut tool_names: Vec<String> = Vec::new();
        for meta_list in &results.metadatas {
            for meta in meta_list {
                // name = meta.get("tool_name", "")
                let name = meta
                    .get("tool_name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                // if name and name not in tool_names: tool_names.append(name)
                if !name.is_empty() && !tool_names.iter().any(|n| n == name) {
                    tool_names.push(name.to_string());
                }
            }
        }
        Ok(tool_names)
    }

    /// `get_tools_for_query(query, k=8, always_include=None)` — the set of tool names
    /// to include for a given user query.
    pub async fn get_tools_for_query(
        &self,
        query: &str,
        k: i64,
        always_include: Option<&HashSet<&str>>,
    ) -> HashSet<String> {
        // base = set(always_include or ALWAYS_AVAILABLE)
        let mut base: HashSet<String> = match always_include {
            Some(set) => set.iter().map(|s| s.to_string()).collect(),
            None => ALWAYS_AVAILABLE.iter().map(|s| s.to_string()).collect(),
        };

        // retrieved = self.retrieve(query, k=k); base.update(retrieved)
        let retrieved = self.retrieve(query, k).await;
        base.extend(retrieved);

        // Keyword-based force-include for common intents. Match on word
        // boundaries, not raw substrings, so short hints like "fix", "line",
        // "serve", "reply" or "unread" don't fire inside unrelated words
        // ("prefix", "deadline"/"online", "observe"/"reserve", "replying",
        // "unreadable"). Same word-boundary matching used in topic_analyzer.
        //   for keywords, tools in self._KEYWORD_HINTS.items():
        //       if any(re.search(rf"\b{re.escape(kw)}\b", ql) for kw in keywords):
        //           base.update(tools)
        // ql = query.lower()
        let ql = query.to_lowercase();
        for (res, tools) in KEYWORD_HINT_RES.iter() {
            if res.iter().any(|re| re.is_match(&ql)) {
                base.extend(tools.iter().map(|t| t.to_string()));
            }
        }

        // Structural scheduling-intent detection.
        // if self._SCHEDULE_RE.search(ql): base.add("manage_tasks")
        if SCHEDULE_RE.is_match(&ql) {
            base.insert("manage_tasks".to_string());
        }
        base
    }
}

// ---------------------------------------------------------------------------
// Singleton
// ---------------------------------------------------------------------------

/// `_tool_index: Optional[ToolIndex] = None` + `_last_attempt = 0.0`.
///
/// The Python keeps two module globals (the cached index + the monotonic timestamp
/// of the last init attempt). Here they live behind one `tokio::sync::Mutex` (the
/// init path is async — `ToolIndex::new` and `index_builtin_tools` await the
/// embedding HTTP probe — so an async-aware lock is required). `_last_attempt`'s
/// `0.0` "never attempted" sentinel maps to `None` (the first call is always due).
struct ToolIndexState {
    tool_index: Option<ToolIndex>,
    last_attempt: Option<Instant>,
}

static STATE: Lazy<Mutex<ToolIndexState>> = Lazy::new(|| {
    Mutex::new(ToolIndexState {
        tool_index: None,
        last_attempt: None,
    })
});

/// `_RETRY_INTERVAL = 30.0` — seconds between re-init attempts.
const RETRY_INTERVAL: u64 = 30;

/// `get_tool_index()` — get or create the singleton `ToolIndex`. Returns `None` if
/// unavailable.
///
/// Returns a `bool` indicating availability rather than a reference: the cached
/// `ToolIndex` lives behind the state mutex (it holds non-`Sync`-friendly async
/// state), so callers drive it through this module. The Python returns the live
/// object; here the singleton is consulted via the guarded state. To run a query,
/// use [`with_tool_index`].
///
/// Mirrors the Python control flow: return the cached healthy index; else, if it has
/// been less than `RETRY_INTERVAL` since the last attempt, return unavailable; else
/// record the attempt and try to (re)build, logging + dropping the index on failure.
pub async fn get_tool_index() -> bool {
    let mut state = STATE.lock().await;

    // if _tool_index is not None and _tool_index.healthy: return _tool_index
    if state.tool_index.as_ref().map(|t| t.healthy()).unwrap_or(false) {
        return true;
    }

    // now = time.monotonic()
    // if now - _last_attempt < _RETRY_INTERVAL: return None
    if let Some(prev) = state.last_attempt {
        if prev.elapsed().as_secs() < RETRY_INTERVAL {
            return false;
        }
    }
    // _last_attempt = now
    state.last_attempt = Some(Instant::now());

    // try: _tool_index = ToolIndex(); _tool_index.index_builtin_tools(); return _tool_index
    // except Exception as e: warn; _tool_index = None; return None
    match ToolIndex::new().await {
        Ok(mut idx) => match idx.index_builtin_tools().await {
            Ok(()) => {
                state.tool_index = Some(idx);
                true
            }
            Err(e) => {
                logger::warning(&format!(
                    "ToolIndex init failed (will retry in {RETRY_INTERVAL}s): {e}"
                ));
                state.tool_index = None;
                false
            }
        },
        Err(e) => {
            logger::warning(&format!(
                "ToolIndex init failed (will retry in {RETRY_INTERVAL}s): {e}"
            ));
            state.tool_index = None;
            false
        }
    }
}

/// Run a closure against the live singleton `ToolIndex`, lazily creating it via the
/// same throttled init path as [`get_tool_index`]. Returns `None` when the index is
/// unavailable (the Python `get_tool_index() -> None` case), matching callers that
/// would otherwise fall through to the keyword path.
///
/// This is the Rust idiom for "use the singleton object" given that the cached index
/// must stay behind the async state mutex: the Python `idx = get_tool_index(); idx.X(...)`
/// pattern becomes `with_tool_index(|idx| ...).await`.
pub async fn with_tool_index<T, F>(f: F) -> Option<T>
where
    F: FnOnce(&ToolIndex) -> T,
{
    if !get_tool_index().await {
        return None;
    }
    let state = STATE.lock().await;
    state.tool_index.as_ref().map(f)
}

/// Prime the warm-embed query path against the singleton (app.py:738's
/// `idx.get_tools_for_query("warmup", 8)`). Lazily creates the index via the same
/// throttled init path as [`get_tool_index`], then runs the `("warmup", 8)` query so
/// the first real user turn hits an already-warm embedding cache. Best-effort: a no-op
/// when the index is unavailable. `with_tool_index` cannot drive this because
/// [`ToolIndex::get_tools_for_query`] is `async` (its closure is sync), so the query
/// runs inside the state lock here.
pub async fn warmup_query() {
    if !get_tool_index().await {
        return;
    }
    let state = STATE.lock().await;
    if let Some(idx) = state.tool_index.as_ref() {
        let _ = idx.get_tools_for_query("warmup", 8, None).await;
    }
}

/// `idx = get_tool_index(); idx.get_tools_for_query(query, k)` — the public async
/// query entry (mirrors `with_tool_index`, but for the `async`
/// [`ToolIndex::get_tools_for_query`], which a sync closure cannot drive). Returns
/// `None` when the index is unavailable so callers fall back to "use all tools".
pub async fn tools_for_query(query: &str, k: i64) -> Option<HashSet<String>> {
    if !get_tool_index().await {
        return None;
    }
    let state = STATE.lock().await;
    match state.tool_index.as_ref() {
        Some(idx) => Some(idx.get_tools_for_query(query, k, None).await),
        None => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn always_available_membership() {
        // Faithful to the Python frozenset contents.
        assert!(ALWAYS_AVAILABLE.contains("bash"));
        assert!(ALWAYS_AVAILABLE.contains("app_api"));
        assert!(ALWAYS_AVAILABLE.contains("list_served_models"));
        // web_fetch is always available alongside web_search.
        assert!(ALWAYS_AVAILABLE.contains("web_fetch"));
        assert!(ALWAYS_AVAILABLE.contains("web_search"));
        assert_eq!(ALWAYS_AVAILABLE.len(), 9);
        assert!(!ALWAYS_AVAILABLE.contains("manage_tasks"));
    }

    #[test]
    fn assistant_always_available_membership() {
        assert!(ASSISTANT_ALWAYS_AVAILABLE.contains("manage_calendar"));
        assert!(ASSISTANT_ALWAYS_AVAILABLE.contains("ui_control"));
        assert_eq!(ASSISTANT_ALWAYS_AVAILABLE.len(), 21);
    }

    #[test]
    fn collection_name_matches() {
        assert_eq!(COLLECTION_NAME, "odysseus_tool_index");
    }

    #[test]
    fn builtin_descriptions_preserve_seen_escape() {
        // The literal backslash in `\Seen` must survive verbatim.
        let mark = BUILTIN_TOOL_DESCRIPTIONS.get("mark_email_read").unwrap();
        assert!(mark.contains("\\Seen"));
        // Spot-check a couple of entries + the total count (matches the Python dict).
        assert!(BUILTIN_TOOL_DESCRIPTIONS.contains_key("bash"));
        assert!(BUILTIN_TOOL_DESCRIPTIONS.contains_key("trigger_research"));
        // web_fetch carries its own embedding description.
        let wf = BUILTIN_TOOL_DESCRIPTIONS.get("web_fetch").unwrap();
        assert!(wf.contains("Fetch and read the text content of a specific URL"));
        assert_eq!(BUILTIN_TOOL_DESCRIPTIONS.len(), 59);
    }

    #[test]
    fn create_document_description_current_text() {
        let cd = BUILTIN_TOOL_DESCRIPTIONS.get("create_document").unwrap();
        // The updated wording (email-draft-aware), not the old "Specify title,
        // language, and content." text.
        assert!(cd.contains("unless an already-open document/email draft is the obvious target"));
        assert!(cd.contains("If an email compose draft is open, edit that draft"));
        assert!(!cd.contains("Specify title, language, and content"));
    }

    #[test]
    fn email_hint_bucket_excludes_tell() {
        // "tell" was removed from the email hint set (#1707) so "tell me ..."
        // requests don't force-include the whole email toolset.
        let (keywords, _tools) = &KEYWORD_HINTS[0];
        assert!(!keywords.contains(&"tell"));
        assert!(keywords.contains(&"email"));
    }

    #[test]
    fn fingerprint_over_sorted_keys() {
        // Reproduce the Python sha256(",".join(sorted(keys))) and confirm hex-ness.
        let mut keys: Vec<&str> = BUILTIN_TOOL_DESCRIPTIONS.keys().copied().collect();
        keys.sort_unstable();
        let joined = keys.join(",");
        let mut hasher = Sha256::new();
        hasher.update(joined.as_bytes());
        let fp = hex::encode(hasher.finalize());
        assert_eq!(fp.len(), 64);
        assert!(fp.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn schedule_re_matches_recurring_intents() {
        // every <word> (typo-resilient), bare clock times, daily/etc.
        assert!(SCHEDULE_RE.is_match("summarize my inbox every dya"));
        assert!(SCHEDULE_RE.is_match("remind me daily"));
        assert!(SCHEDULE_RE.is_match("ping me at 7:30 am"));
        assert!(SCHEDULE_RE.is_match("at 7am please"));
        assert!(SCHEDULE_RE.is_match("each morning"));
        // Not a schedule.
        assert!(!SCHEDULE_RE.is_match("just send the email now"));
    }

    #[test]
    fn keyword_hints_email_bucket() {
        // The first hint bucket maps email/mail/... to the email tool cluster.
        let (keywords, tools) = &KEYWORD_HINTS[0];
        assert!(keywords.contains(&"email"));
        assert!(keywords.contains(&"inbox"));
        assert!(tools.contains(&"send_email"));
        assert!(tools.contains(&"resolve_contact"));
        // 21 buckets total (matches the Python _KEYWORD_HINTS dict).
        assert_eq!(KEYWORD_HINTS.len(), 21);
    }

    /// Mirror of the `get_tools_for_query` keyword-hint loop, against the compiled
    /// word-boundary matchers, so we can assert matching behaviour without a live
    /// (HTTP-backed) `ToolIndex`. `ql` is pre-lowercased like the real path.
    fn hinted_tools(query: &str) -> HashSet<&'static str> {
        let ql = query.to_lowercase();
        let mut out: HashSet<&'static str> = HashSet::new();
        for (res, tools) in KEYWORD_HINT_RES.iter() {
            if res.iter().any(|re| re.is_match(&ql)) {
                out.extend(tools.iter().copied());
            }
        }
        out
    }

    #[test]
    fn keyword_hints_match_on_word_boundaries() {
        // Whole-word hits force-include the bucket.
        assert!(hinted_tools("check my email").contains("send_email"));
        assert!(hinted_tools("any unread mail").contains("read_email"));
        assert!(hinted_tools("serve the model").contains("serve_model"));
        assert!(hinted_tools("fix the bug").contains("edit_document"));

        // Substring-only occurrences must NOT fire (the whole point of #1707-era
        // word-boundary matching): "unread" inside "unreadable", "reply" inside
        // "replying", "serve" inside "observe"/"reserve", "fix" inside "prefix",
        // "line" inside "deadline".
        assert!(!hinted_tools("this text is unreadable").contains("read_email"));
        assert!(!hinted_tools("please observe the reserve carefully").contains("serve_model"));
        // "prefix" contains "fix"; "deadline"/"online" contain "line" — neither may
        // force-include the document tools (matches test_tool_index_keyword_boundaries.py).
        // NB: "add a" IS a genuine edit_document keyword, so the phrase must avoid it.
        for q in ["prefix the output with a label", "the deadline is online already"] {
            assert!(!hinted_tools(q).contains("edit_document"), "{q}");
            assert!(!hinted_tools(q).contains("update_document"), "{q}");
        }
    }

    #[test]
    fn tell_no_longer_triggers_email_tools() {
        // #1707: "visit <url> and tell me the title" must not pull in the email
        // toolset. "tell" was removed from the email hint set and word-boundary
        // matching means it can't sneak back in via a substring either.
        let tools = hinted_tools("visit example.com and tell me the title");
        assert!(!tools.contains("send_email"));
        assert!(!tools.contains("list_emails"));
        assert!(!tools.contains("read_email"));
    }

    #[test]
    fn keyword_hint_res_mirrors_hints() {
        // The compiled matcher table is index-for-index aligned with KEYWORD_HINTS.
        assert_eq!(KEYWORD_HINT_RES.len(), KEYWORD_HINTS.len());
        for ((kws, tools), (res, res_tools)) in KEYWORD_HINTS.iter().zip(KEYWORD_HINT_RES.iter()) {
            assert_eq!(kws.len(), res.len());
            assert_eq!(tools, *res_tools);
        }
    }
}
