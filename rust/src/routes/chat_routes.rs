// routes/chat_routes.rs  <- routes/chat_routes.py
//! Chat routes — `setup_chat_routes() -> Router<AppState>` (app.py include #8).
//!
//! Faithful translation of `routes/chat_routes.py`'s `APIRouter(tags=["chat"])`
//! factory. **SUPERSEDES the inline `web/mod.rs` `POST /api/chat_stream`** (the
//! wave-5 demo stream): the RECONCILE step deletes that inline handler + its
//! `owns`/`parse_form`/`sse_bytes` helpers and mounts this module at app.py
//! ordinal 8. Every captured factory argument (`session_manager`, `chat_handler`,
//! `chat_processor`, `memory_manager`, `research_handler`, `upload_handler`,
//! `memory_vector`, `webhook_manager`, `skills_manager`) is reached through
//! [`AppState`], so the factory takes no parameters (mirroring the wave-3/4
//! `setup_research_routes` / `setup_model_routes` shape).
//!
//! ## Port classification — web + db
//! The module reaches the `ChatHandler`/`ChatProcessor` on the always-compiled
//! [`AppState`], reads/writes the DB (`sessions.mode`, the `documents` active-doc
//! lookup, the image-`ModelEndpoint` probe, the `/api/search` query), and binds to
//! the `chat_helpers` module. The crate has no cargo feature flags, so the whole
//! module is always compiled, exactly like `session_routes`/`history_routes`.
//!
//! ## THE CRITICAL UNION (`chat_stream`) — Python logic PLUS the Odysseus-Rust
//! ## codex/agent dispatch
//! `chat_routes.py::chat_stream` covers research / image / agent / normal chat over
//! SSE, plus `_active_streams` / `_safe_stream` / `_TOOL_INTENT_PATTERNS`. The
//! INLINE `web/mod.rs::chat_stream` (now superseded) carried the **Odysseus-Rust
//! enhancement** that is NOT in the Python: the Codex provider dispatch. The ported
//! `chat_stream` here is the **UNION** — the full Python chat logic with that
//! dispatch folded into the agent/chat branch selection:
//!
//!   * `is_codex = codex::is_codex_url(url)` → **Mode A** (the `codex app-server`
//!     harness; codex runs its OWN tools server-side) via `codex::stream_chat`.
//!     Mode A is already agentic, so it keeps its native path and never enters the
//!     tool loop.
//!   * `is_codex_responses = codex::is_codex_responses_url(url)` → **Mode B** (the
//!     ChatGPT Responses backend over HTTPS). It is NOT `is_codex`, so it
//!     participates in `is_agent`; in chat mode it rides `stream_llm`, which
//!     dispatches to `stream_codex_responses` on the raw `codex-responses:` url.
//!   * `is_agent` (the Python `chat_mode == "agent"`, and `!is_codex`) →
//!     `stream_agent_loop` with the Odysseus tools (the `disabled_tools` gating from
//!     `allow_bash`/`allow_web_search` + privileges).
//!   * else → `stream_llm` (the Python `chat_mode == "chat"` branch).
//!
//! Documented at each branch below as an Odysseus-Rust enhancement. Mode A, Mode B,
//! and agent mode are all preserved.
//!
//! ## LIVE endpoint/fallback resolution (was deferred)
//! * **Research endpoint resolution** — `_resolve_research_endpoint(sess)` now wires
//!   `endpoint_resolver::resolve_endpoint("research", None)` (the ported settings
//!   cascade + `ModelEndpoint` lookup). The resolver returns `None` whenever no admin
//!   `research_endpoint_id` is configured (the common case), and the caller
//!   substitutes the session's own `(endpoint_url, model, headers)` — exact parity
//!   with Python, which passed those same sess.* values in as `fallback_*`. This
//!   makes the resolved triple LIVE for the **streaming** research path
//!   (`synthesize_query` / `start_research` / the poll loop, all ported + bound to
//!   the landed `ResearchHandler`).
//! * **`stream_llm_with_fallback` candidate chain** — LIVE: the chat path now resolves
//!   the configured fallback chain via `endpoint_resolver::resolve_chat_fallback_candidates()`
//!   (chat_routes.py:624-628). Python wraps it in `try/except: []`; the Rust fn cannot
//!   panic and returns `[]` when unconfigured, so the except is moot — the unconfigured
//!   outcome (stream only the session's primary `(endpoint_url, model, headers)`) is
//!   preserved, while a configured chain now lets a stream survive a primary that dies
//!   before output (agent-mode `fallbacks:` + chat-mode `cands.extend`).
//!
//! ## Notes
//! * **Non-streaming `/api/chat` research injection** — LIVE: `resolve_research_endpoint`
//!   + the public [`crate::src::research_handler::ResearchHandler::run_research_once`]
//!   run a one-shot deep-research pass and insert it as an untrusted-context message
//!   at `len(preface)` (the Python `try/except` is preserved — the report string is
//!   always returned, never a panic).
//! * **`set_user_tz_offset`** (calendar) — LIVE: the `x-tz-offset` header is stashed
//!   via [`crate::routes::calendar_routes::set_user_tz_offset`] (fire-and-forget).
//! * **`get_active_document()` in-memory fallback** — bound to the ported
//!   [`crate::src::tool_implementations::documents::get_active_document`].
//!
//! ## SUPERSEDES (the inline subset RECONCILE removes from `web/mod.rs`)
//! `POST /api/chat_stream`. The Codex `auto_register_codex` / `register_codex_providers`
//! / `codex_connect` (the `/api/codex/connect` action) stay inline — they are NOT
//! part of `chat_routes.py` (Codex is a Rust-side integration). RECONCILE keeps
//! `/api/codex/connect` registered inline.


use std::collections::HashSet;

use axum::body::Body;
use axum::extract::{Multipart, Path, Query, State};
use axum::http::header;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Extension, Json, Router};

use futures_util::StreamExt;
use once_cell::sync::Lazy;
use serde_json::{json, Map, Value};

use crate::core::database::session_local;
use crate::core::models::ChatMessage;
use crate::routes::auth_adapter;
use crate::routes::chat_helpers::{
    self, BuildContextOpts, PostResponseOpts, SaveAssistantOpts,
};
use crate::routes::{AppState, CurrentUser, HttpException};
use crate::src::request_models::ChatRequest;

/// A `/api/search` result row: `(session_id, session_name, role, content, timestamp)`.
type SearchRow = (String, Option<String>, String, String, Option<String>);

/// A `documents` row for the active-doc lookup:
/// `(id, current_content, title, language, session_id)`.
type DocRow = (
    String,
    Option<String>,
    Option<String>,
    Option<String>,
    Option<String>,
);

// ===========================================================================
// Module-global state — `_active_streams` (the partial-save safety-net registry)
// ===========================================================================

/// `_active_streams: Dict[str, dict]` — track active streams for the partial-save
/// safety net. The Python factory closes over one module dict; the axum factory is
/// called once at startup, so this is process-global `Lazy<Mutex<...>>` — one
/// registry per running server, exactly like one factory call in Python.
static ACTIVE_STREAMS: Lazy<std::sync::Mutex<std::collections::HashMap<String, Value>>> =
    Lazy::new(|| std::sync::Mutex::new(std::collections::HashMap::new()));

/// `_stream_set(session_id, **fields)` — update fields on the active-stream entry
/// for `session_id`, or no-op if the entry has already been popped (the `.get()`
/// avoids the KeyError race the Python docstring describes).
fn stream_set(session_id: &str, fields: &[(&str, Value)]) {
    let mut streams = ACTIVE_STREAMS.lock().unwrap();
    if let Some(Value::Object(rec)) = streams.get_mut(session_id) {
        for (k, v) in fields {
            rec.insert((*k).to_string(), v.clone());
        }
    }
}

/// `_active_streams[session_id] = {...}` — register/replace the entry.
fn stream_register(session_id: &str, entry: Value) {
    ACTIVE_STREAMS
        .lock()
        .unwrap()
        .insert(session_id.to_string(), entry);
}

/// `_active_streams.pop(session_id, None)`.
fn stream_pop(session_id: &str) {
    ACTIVE_STREAMS.lock().unwrap().remove(session_id);
}

/// `session_id in _active_streams` → the entry clone (so `chat_stream_status` can
/// return it).
fn stream_get(session_id: &str) -> Option<Value> {
    ACTIVE_STREAMS.lock().unwrap().get(session_id).cloned()
}

// ===========================================================================
// `PartialSaveGuard` — the cancelled/dropped-stream partial-save safety net
// ===========================================================================

/// RAII stand-in for the Python `except (asyncio.CancelledError, GeneratorExit):`
/// blocks that wrap BOTH the chat-mode (chat_routes.py:774-781) and agent-mode
/// (861-877) relay loops. When the SSE generator is dropped/cancelled mid-stream
/// before reaching `[DONE]` (the client navigated away, the run was stopped), the
/// accumulated partial `full_response` must still be persisted as an assistant
/// message tagged `{"stopped": true, "model": ...}` — exactly the Python's
/// `sess.add_message(ChatMessage("assistant", _stopped_content, _stopped_md))`.
///
/// The Python achieves this by catching the cancellation; in Rust a dropped
/// `async_stream` future just stops being polled — there is no catchable cancel
/// signal — so we hang the save off `Drop`. `Drop` cannot be async, so it
/// fire-and-forgets a `tokio::spawn` that runs the same append-and-persist the
/// Python partial-save runs. The Python persists via `sess.add_message(...)` —
/// the `Session.add_message` (core/models.py:64) that appends to the (cached)
/// session's `history` and delegates to `SessionManager._persist_message`. That
/// path does NOT call `get_session`/`_touch_session`, so the only `last_accessed`
/// bump comes from `_persist_message` itself (one bump, not two). We mirror that
/// exactly: `with_session_mut(session_id, |s| s.history.push(...))` appends to the
/// CACHED session (the Python `sess.history.append`), then `_persist_message`
/// writes the message row + the single `last_accessed`/`last_message_at` bump. We
/// deliberately avoid `SessionManager::add_message`, whose `get_session` →
/// `_touch_session` would add a redundant `last_accessed` write the Python
/// partial-save never performs.
///
/// The buffer is shared (`Arc<Mutex<String>>`) with the relay loop so `Drop` sees
/// whatever has accumulated so far. [`PartialSaveGuard::disarm`] is called on the
/// normal `[DONE]` completion path (which already saves via
/// `save_assistant_response`), so a clean finish does NOT double-save.
struct PartialSaveGuard {
    /// Shared accumulator — the relay loop pushes deltas here; `Drop` reads it.
    full_response: std::sync::Arc<std::sync::Mutex<String>>,
    sessions: std::sync::Arc<crate::core::session_manager::SessionManager>,
    session_id: String,
    model: String,
    armed: bool,
}

impl PartialSaveGuard {
    fn new(
        full_response: std::sync::Arc<std::sync::Mutex<String>>,
        sessions: std::sync::Arc<crate::core::session_manager::SessionManager>,
        session_id: String,
        model: String,
    ) -> Self {
        PartialSaveGuard {
            full_response,
            sessions,
            session_id,
            model,
            armed: true,
        }
    }

    /// Stop the guard from persisting on `Drop` — called once the normal `[DONE]`
    /// path has saved the response itself.
    fn disarm(&mut self) {
        self.armed = false;
    }
}

impl Drop for PartialSaveGuard {
    fn drop(&mut self) {
        if !self.armed {
            return;
        }
        // `if full_response:` — only persist a non-empty partial.
        let partial = self
            .full_response
            .lock()
            .map(|g| g.clone())
            .unwrap_or_default();
        if partial.is_empty() {
            return;
        }
        let sessions = self.sessions.clone();
        let session_id = self.session_id.clone();
        let model = self.model.clone();
        // Drop can't be async → fire-and-forget the persist. Spawn only when a
        // tokio runtime is current (the detached run always executes inside one);
        // outside a runtime (e.g. a sync test) the save is skipped rather than
        // panicking.
        if let Ok(handle) = tokio::runtime::Handle::try_current() {
            handle.spawn(async move {
                crate::pylog::info(&format!(
                    "Client disconnected mid-stream for session {}, saving partial response ({} chars)",
                    session_id,
                    partial.chars().count()
                ));
                // `_stopped_content, _stopped_md = clean_thinking_for_save(
                //     full_response, {"stopped": True, "model": sess.model})`
                let mut md = Map::new();
                md.insert("stopped".to_string(), json!(true));
                md.insert("model".to_string(), json!(model));
                let (content, meta) = chat_helpers::clean_thinking_for_save(&partial, Some(&md));
                // `sess.add_message(ChatMessage("assistant", content, metadata=meta))`
                // — the `Session.add_message` (core/models.py:64) pair, NOT
                // `SessionManager.add_message`. Append to the CACHED session's history
                // (`sess.history.append` + `message_count = len(history)`) WITHOUT
                // going through `get_session`/`_touch_session`, then persist the row
                // via `_persist_message` (which performs the single
                // `last_accessed`/`last_message_at` bump Python's partial-save does).
                // The Python wraps the agent-block save in its own try/except so a
                // save failure doesn't mask the cancellation — there is nothing to
                // unwrap here, so a `with_session_mut` miss (session evicted) is just a
                // no-op, then `_persist_message` swallows any DB error internally.
                let msg = ChatMessage::new("assistant", content, Some(meta));
                sessions.with_session_mut(&session_id, |s| {
                    s.history.push(msg.clone());
                    s.message_count = s.history.len() as i64;
                });
                // `session_manager._persist_message(self.id, message)` — the direct
                // message-row write (the `_persist_message`-equivalent). Reached
                // through the `SessionPersistence` trait the manager implements.
                use crate::core::models::SessionPersistence as _;
                sessions._persist_message(&session_id, &msg);
                // `session_manager.save_sessions()` — the no-op DB save (Python's
                // `if not incognito: session_manager.save_sessions()`; the ported
                // `save_sessions` is a no-op, so the `incognito` guard is moot here).
            });
        }
    }
}

// ===========================================================================
// `_TOOL_INTENT_PATTERNS` — phrases that escalate plain chat → agent
// ===========================================================================

/// `_TOOL_INTENT_PATTERNS` — the regex list that signals the user wants a todo /
/// reminder / calendar event / email / UI-panel / deep-research / shell action.
/// 1:1 port of the REWRITTEN `src/action_intents.py::_TOOL_INTENT_PATTERNS`. Each
/// Python `re.compile(pattern, re.I)` becomes a `regex::Regex` with the `(?i)`
/// inline flag prepended (the `re.I` equivalent). When any matches in plain chat
/// mode, the route silently escalates to the agent loop so `manage_notes` /
/// `manage_calendar` etc. are in scope.
///
/// The Python builds the patterns from shared fragments:
///   `_ACTION_QUESTION = r"\b(?:can|could|would|will)\s+you\s+"`
///   `_PLEASE = r"^\s*(?:please\s+)?"`
///   `_CALENDAR_ACTION = r"(?:add|create|schedule|book|put|set\s+up|make)"`
///   `_CALENDAR_THING = r"(?:calendar|calendar\s+(?:entry|item)|event|meeting|appointment|entry|call)"`
///   `_PANEL = r"(?:calendar|notes?|inbox|email|mail|documents?|docs|library|gallery|settings|cookbook|sessions?|chats?|skills|memories|memory|brain)"`
/// We inline each fragment into the final pattern strings so each entry is the
/// exact concatenation Python produces, with `(?i)` prepended.
static TOOL_INTENT_PATTERNS: Lazy<Vec<regex::Regex>> = Lazy::new(|| {
    // Shared fragments (verbatim from action_intents.py).
    const ACTION_QUESTION: &str = r"\b(?:can|could|would|will)\s+you\s+";
    const PLEASE: &str = r"^\s*(?:please\s+)?";
    const CALENDAR_ACTION: &str = r"(?:add|create|schedule|book|put|set\s+up|make)";
    const CALENDAR_THING: &str =
        r"(?:calendar|calendar\s+(?:entry|item)|event|meeting|appointment|entry|call)";
    const PANEL: &str = r"(?:calendar|notes?|inbox|email|mail|documents?|docs|library|gallery|settings|cookbook|sessions?|chats?|skills|memories|memory|brain)";

    let pats: Vec<String> = vec![
        // Calendar/event creation. Covers "Can you add an entry to my
        // calendar?" and imperatives like "add lunch to my calendar".
        format!(r"(?i){ACTION_QUESTION}{CALENDAR_ACTION}\b.{{0,120}}\b{CALENDAR_THING}\b"),
        format!(r"(?i){PLEASE}{CALENDAR_ACTION}\b.{{0,120}}\b(?:to|on|in|into|for)\s+(?:my\s+|the\s+|this\s+)?calendar\b"),
        format!(r"(?i){PLEASE}{CALENDAR_ACTION}\s+(?:a\s+|an\s+)?(?:calendar\s+)?(?:event|meeting|appointment|entry|item|call)\b"),
        r"(?i)\bput\s+.+\bon\s+(?:my\s+)?calendar\b".to_string(),

        // Notes, todos, checklists, and reminders.
        r"(?i)\bremind\s+me\b".to_string(),
        format!(r"(?i){ACTION_QUESTION}(?:add|create|make|take|jot|write\s+down|set)\b.{{0,120}}\b(?:note|todo|task|checklist|reminder)\b"),
        format!(r"(?i){PLEASE}(?:add|create|make)\s+(?:a\s+|an\s+)?(?:todo|task|reminder|note|checklist)\b"),
        format!(r"(?i){PLEASE}(?:take|jot|write\s+down)\s+(?:a\s+|an\s+)?note\b"),
        format!(r"(?i){PLEASE}(?:add|jot|write\s+down)\b.{{0,120}}\b(?:to|in|into)\s+(?:my\s+|the\s+)?(?:todo(?:\s+list)?|task\s+list|notes?|checklist)\b"),
        format!(r"(?i){PLEASE}set\s+(?:a\s+)?reminder\b"),
        format!(r"(?i){ACTION_QUESTION}set\s+(?:a\s+)?reminder\b"),

        // Email actions.
        format!(r"(?i){ACTION_QUESTION}(?:send|write|reply|email|message|archive|delete|mark)\b.{{0,120}}\b(?:emails?|mail|messages?|inbox|unread|read)\b"),
        format!(r"(?i){PLEASE}(?:send|write|reply)\b.{{0,120}}\b(?:emails?|mail|messages?)\b"),
        format!(r"(?i){PLEASE}(?:archive|delete|mark)\b.{{0,120}}\b(?:emails?|mail|messages?|inbox)\b"),
        r"(?i)\b(?:send|write|reply)\s+(?:an?\s+)?(?:email|message|mail)\b".to_string(),
        r"(?i)\bemail\s+\w+\b".to_string(),
        r"(?i)\bcheck\s+(?:my\s+)?(?:email|inbox|mail)\b".to_string(),
        r"(?i)\bunread\s+(?:email|mail)s?\b".to_string(),

        // UI/control-plane actions that should open panels or flip toggles.
        format!(r"(?i){PLEASE}(?:open|show|bring\s+up)\s+(?:me\s+)?(?:my\s+|the\s+)?{PANEL}\b"),
        r"(?i)\b(?:disable|enable|turn\s+(?:on|off))\s+(?:the\s+)?(?:shell|search|web|browser|documents?|memory|skills|images?|calendar|email|mail|research|incognito)\b".to_string(),

        // Deep research jobs, not quick conceptual mentions of research.
        format!(r"(?i){PLEASE}(?:research|deep\s+dive|look\s+into|investigate)\s+.+"),
        format!(r"(?i){ACTION_QUESTION}(?:research|do\s+research|deep\s+dive|look\s+into|investigate)\s+.+"),

        // Shell / remote-host intent.
        r"(?i)\bssh\s+(?:in)?to\b".to_string(),
        r"(?i)\bssh\s+\w+".to_string(),
        r"(?i)\b(run|execute)\s+.{1,40}\bon\s+\w+".to_string(),
        r"(?i)\b(can|could|please|would)\s+you\s+(run|execute|exec)\b".to_string(),
        // Shell verbs only count in imperative position (start of message,
        // optionally after "please") or as a "can you ..." request. A bare
        // word match promoted informational questions ("What does the grep
        // command do?") and incidental uses ("My cat ate my homework").
        format!(r"(?i){PLEASE}(deploy|build|install|restart|reboot|kill|tail|grep|cat|ls|cd|cp|mv|rm)\b\s+\S+"),
        format!(r"(?i){ACTION_QUESTION}(deploy|build|install|restart|reboot|kill|tail|grep|cat|ls|cd|cp|mv|rm)\b\s+\S+"),
        r"(?i)\b(check|see)\s+(if|whether|what)\s+.{1,40}\b(running|process|service|port|file|exists?)\b".to_string(),
    ];
    pats.iter().map(|p| regex::Regex::new(p).unwrap()).collect()
});

/// `_message_needs_tools(text)` — `any(p.search(text) for p in _TOOL_INTENT_PATTERNS)`.
fn message_needs_tools(text: &str) -> bool {
    // `if not text: return False`
    if text.is_empty() {
        return false;
    }
    TOOL_INTENT_PATTERNS.iter().any(|p| p.is_match(text))
}

// ===========================================================================
// Router factory
// ===========================================================================

/// `setup_chat_routes(...) -> APIRouter` — assemble the chat router.
///
/// app.py include #8. The Python factory's nine manager/handler arguments are
/// reached via [`AppState`], so this takes none. The route order mirrors the
/// Python registration order exactly (`/api/chat`, `/api/chat_stream`,
/// `/api/chat/resume/:session_id`, `/api/chat/stop/:session_id`,
/// `/api/chat/stream_status/:session_id`, `/api/inject_context/:session_id`,
/// `/api/search`, `/api/rewrite`).
pub fn setup_chat_routes() -> Router<AppState> {
    Router::new()
        .route("/api/chat", post(chat_endpoint))
        .route("/api/chat_stream", post(chat_stream))
        .route("/api/chat/resume/:session_id", get(chat_resume))
        .route("/api/chat/stop/:session_id", post(chat_stop))
        .route("/api/chat/stream_status/:session_id", get(chat_stream_status))
        .route("/api/inject_context/:session_id", post(inject_context))
        .route("/api/search", get(search_messages))
        .route("/api/rewrite", post(rewrite_message))
}

// ===========================================================================
// Small shared helpers
// ===========================================================================

/// `get_current_user(request)` — the resolved username (`None` when the auth gate
/// could not stamp `CurrentUser`).
fn current_user(user: &Option<Extension<CurrentUser>>) -> Option<String> {
    user.as_ref().map(|Extension(CurrentUser(u))| u.clone())
}

/// Collect all `multipart/form-data` text fields into a map (the FastAPI
/// `await request.form()` surface). Mirrors `web/mod.rs::parse_form`.
async fn parse_form(mut mp: Multipart) -> std::collections::HashMap<String, String> {
    let mut out = std::collections::HashMap::new();
    while let Ok(Some(field)) = mp.next_field().await {
        let name = field.name().unwrap_or("").to_string();
        if name.is_empty() {
            continue;
        }
        if let Ok(text) = field.text().await {
            out.insert(name, text);
        }
    }
    out
}

/// `str(form_data.get(key, "")).lower() == "true"` — the form-bool truthiness chat
/// uses pervasively (an absent field is `""`, falsy).
fn form_truthy(f: &std::collections::HashMap<String, String>, key: &str) -> bool {
    f.get(key).map(|v| v.to_lowercase() == "true").unwrap_or(false)
}

/// `form_data.get(key)` then Python-truthiness on the RAW string. The Python
/// `chat_stream` reads `use_web = form_data.get("use_web")` (the raw `str | None`)
/// and feeds it to `build_chat_context`, where `use_web and not skip_web` gates the
/// search via `if use_web:`. A non-empty string — even `"false"` — is TRUTHY in
/// Python, so only an absent (`None`) or empty (`""`) value disables web. This
/// mirrors that: `true` iff the field is present and non-empty.
fn form_present_truthy(f: &std::collections::HashMap<String, String>, key: &str) -> bool {
    f.get(key).map(|v| !v.is_empty()).unwrap_or(false)
}

/// `String -> Ok(Bytes)` for the SSE body stream (infallible). Mirrors the inline
/// `web/mod.rs::sse_bytes` the RECONCILE deletes.
fn sse_bytes(s: &str) -> Result<bytes::Bytes, std::convert::Infallible> {
    Ok(bytes::Bytes::from(s.to_owned()))
}

/// `_resolve_research_endpoint(sess)` — return `(endpoint_url, model, headers)` for
/// Deep Research (research_routes.py:37-45).
///
/// LIVE: the Python calls `resolve_endpoint("research", fallback_url=sess.endpoint_url,
/// fallback_model=sess.model, fallback_headers=sess.headers)`. The ported
/// `endpoint_resolver::resolve_endpoint` dropped the always-None `fallback_*`
/// params (owner stays None — `_resolve_research_endpoint` passes sess.* only as
/// fallbacks, NEVER as owner), so the caller substitutes the session's own
/// `(endpoint_url, model, headers)` whenever the resolver returns None (no admin
/// `research_endpoint_id` configured — the common case). This is exact parity with
/// Python, which passed those same sess.* values INTO `resolve_endpoint` as the
/// fallbacks. Feeds the STREAMING research path (synthesize_query / start_research).
fn resolve_research_endpoint(
    sess: &crate::core::models::Session,
) -> (String, String, indexmap::IndexMap<String, String>) {
    let (u, m, h) = crate::src::endpoint_resolver::resolve_endpoint("research", None);
    (
        u.unwrap_or_else(|| sess.endpoint_url.clone()),
        m.unwrap_or_else(|| sess.model.clone()),
        h.unwrap_or_else(|| sess.headers.clone()),
    )
}

/// `messages.insert(idx, {role, content})` for an [`untrusted_context_message`]
/// `Value` (the dict the prompt-security helper returns).
fn insert_at(messages: &mut Vec<Value>, idx: usize, msg: Value) {
    let idx = idx.min(messages.len());
    messages.insert(idx, msg);
}

// ===========================================================================
// POST /api/chat — non-streaming
// ===========================================================================

/// `POST /api/chat` — the non-streaming chat endpoint.
///
/// `_verify_session_owner` → load → `_enforce_chat_privileges` → inline-memory
/// command short-circuit → `build_chat_context` → research injection → a single
/// `llm_call_async` → save + `run_post_response_tasks`. Returns `{"response":
/// reply}`.
async fn chat_endpoint(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Json(chat_request): Json<ChatRequest>,
) -> Result<Response, HttpException> {
    let user = current_user(&user);

    // chat_request fields.
    let message = chat_request.message;
    let session = chat_request.session;
    let att_ids: Vec<String> = chat_request.attachments;
    let use_web = chat_request.use_web;
    let use_research = chat_request.use_research;
    let time_filter = chat_request.time_filter;
    let preset_id = chat_request.preset_id;

    // `_verify_session_owner(request, session)` — ownership before load.
    auth_adapter::verify_session_owner(&s, user.as_deref(), &session)?;

    // `sess = session_manager.get_session(session)` (KeyError -> 404).
    let mut sess = s
        .sessions
        .get_session(&session)
        .map_err(|_| HttpException::new(404, format!("Session '{session}' not found")))?;

    // `if _clear_orphaned_session_endpoint(sess, owner=owner): raise HTTPException(400, ...)`
    // — the session points at a deleted endpoint; clear it and bail.
    if clear_orphaned_session_endpoint(&mut sess, user.as_deref()) {
        return Err(HttpException::new(
            400,
            "Selected model endpoint was removed. Pick another model in Settings.",
        ));
    }

    // Empty model + live endpoint = setup race (Issue #587). Repair from the
    // endpoint's cached model list BEFORE the privilege check, which otherwise
    // sees "" and behaves inconsistently with the allowlist.
    recover_empty_session_model(&mut sess, &session, user.as_deref());
    if sess.model.trim().is_empty() {
        return Err(HttpException::new(
            400,
            "No model selected for this chat. Open the model picker and choose one before sending.",
        ));
    }

    // `_enforce_chat_privileges(request, sess)`.
    chat_helpers::enforce_chat_privileges(&s.auth, user.as_deref(), &sess.model)?;

    // Inline memory command — short-circuits the whole pipeline.
    if let Ok(Some(memory_response)) = s
        .chat_handler
        .handle_memory_command(&mut sess, &message)
        .await
    {
        return Ok(Json(json!({ "response": memory_response })).into_response());
    }

    // `ctx = await build_chat_context(...)` — the sync path uses text_for_context
    // (use_enhanced_message defaults False).
    let mut ctx = chat_helpers::build_chat_context(
        &mut sess,
        user.as_deref(),
        &s.chat_handler,
        &s.chat_processor,
        &message,
        &session,
        BuildContextOpts {
            preset_id: preset_id.as_deref(),
            att_ids: Some(&att_ids),
            use_web: Some(use_web),
            time_filter: time_filter.as_deref(),
            webhook_manager: Some(&s.webhook_manager),
            ..Default::default()
        },
    )
    .await?;

    // Research injection (chat_routes.py:147-159): run a one-shot deep-research
    // pass over the resolved research endpoint and insert the report as an
    // untrusted-context message at index `len(preface)`. Wrapped like the Python
    // `try/except` — `run_research_once` returns a (possibly fallback) report
    // string and never panics, so a failure surfaces as that string, not a crash.
    if use_research {
        let (r_ep, r_model, r_headers) = resolve_research_endpoint(&sess);
        let rh = crate::src::research_handler::ResearchHandler::new();
        let research_ctx = rh.run_research_once(&message, &r_ep, &r_model, r_headers).await;
        let idx = ctx.preface.len();
        let msg = crate::src::prompt_security::untrusted_context_message(
            "research context",
            Some(&research_ctx),
        );
        insert_at(&mut ctx.messages, idx, msg);
    }

    // `reply = await llm_call_async(sess.endpoint_url, sess.model, ctx.messages, ...)`.
    let reply = crate::src::llm_core::llm_call_async(
        &sess.endpoint_url,
        &sess.model,
        ctx.messages.clone(),
        ctx.preset.temperature.unwrap_or(0.7),
        ctx.preset.max_tokens.unwrap_or(0),
        sess.headers.clone(),
        300,
    )
    .await
    .map_err(|e| HttpException::new(500, e.to_string()))?;

    // `_clean_reply, _clean_md = clean_thinking_for_save(reply, {"model": sess.model})`.
    let mut base_md = Map::new();
    base_md.insert("model".to_string(), json!(sess.model));
    let (clean_reply, clean_md) = chat_helpers::clean_thinking_for_save(&reply, Some(&base_md));
    sess.add_message(ChatMessage::new(
        "assistant",
        clean_reply,
        if clean_md.is_empty() { None } else { Some(clean_md) },
    ));

    // `update_session_last_accessed(session); session_manager.save_sessions()`.
    crate::core::database::update_session_last_accessed(&session);
    s.sessions.save_sessions();

    // Mirror the completed turn (user + assistant) into the in-memory cache. As in
    // `chat_stream`, `get_session` returned a CLONE (Python yields the LIVE cached
    // object), so without this the cache's history stays empty and the cache-only
    // readers `fork_session` / `peek_session` (no DB hydration) copy 0 messages.
    s.sessions.with_session_mut(&session, |cached| {
        cached.history = sess.history.clone();
        cached.message_count = sess.message_count;
    });

    // Background tasks (memory, webhook, auto-name).
    chat_helpers::run_post_response_tasks(
        &sess,
        s.sessions.clone(),
        &session,
        &message,
        &reply,
        None,
        &ctx.uprefs,
        s.memory_manager.clone(),
        s.memory_vector.clone(),
        Some(s.webhook_manager.clone()),
        PostResponseOpts {
            character_name: ctx.preset.character_name.as_deref(),
            owner: ctx.user.as_deref(),
            ..Default::default()
        },
    );

    Ok(Json(json!({ "response": reply })).into_response())
}

// ===========================================================================
// POST /api/chat_stream — THE CRITICAL UNION (research / image / agent / chat
// over SSE, PLUS the Odysseus-Rust codex/agent dispatch)
// ===========================================================================

/// `POST /api/chat_stream` — the streaming chat endpoint.
async fn chat_stream(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    headers: axum::http::HeaderMap,
    mp: Multipart,
) -> Result<Response, HttpException> {
    let user = current_user(&user);

    // x-tz-offset stash (chat_routes.py:206-212): so `manage_notes`/`manage_calendar`
    // parse natural-language times in the USER's tz, not the server's. Present-header
    // -> set; fire-and-forget like the Python `try/except`. (`HeaderMap` is a
    // non-body extractor, so it precedes the body-consuming `Multipart`.)
    if let Some(tz) = headers.get("x-tz-offset").and_then(|v| v.to_str().ok()) {
        crate::routes::calendar_routes::set_user_tz_offset(tz);
    }

    // The Python reads a JSON body OR the form; in the axum app the body arrives as
    // `multipart/form-data` (the frontend posts a FormData). We parse the form once;
    // the `body`-attachments branch (`body["attachments"]` as a JSON list) is folded
    // into the same `att_ids` resolution below via the `attachments` form field.
    let f = parse_form(mp).await;

    let message_raw = f.get("message").cloned();
    let session_raw = f.get("session").cloned();
    let attachments = f.get("attachments").cloned();
    // Python: `use_web = form_data.get("use_web")` — the RAW string, threaded into
    // `build_chat_context` as `use_web and not skip_web`. A present, non-empty value
    // (even `"false"`) is truthy in Python, so web stays enabled; only an absent or
    // empty value disables it. `form_present_truthy` reproduces that exactly.
    let use_web = form_present_truthy(&f, "use_web");
    let use_research = f.get("use_research").cloned().unwrap_or_default();
    let time_filter = f.get("time_filter").cloned();
    let preset_id = f.get("preset_id").cloned();
    let allow_bash = form_truthy(&f, "allow_bash");
    let allow_web_search = form_truthy(&f, "allow_web_search");
    let use_rag = f.get("use_rag").cloned();
    let search_context = f.get("search_context").cloned();
    let compare_mode = form_truthy(&f, "compare_mode");
    let incognito = form_truthy(&f, "incognito");
    let mut chat_mode = f.get("mode").cloned().unwrap_or_default().to_lowercase();

    // `user_requested_agent = (chat_mode == "agent")` — captured BEFORE auto-escalation.
    let user_requested_agent = chat_mode == "agent";

    // Intent auto-escalation: chat → agent for a notes/calendar/email/shell intent.
    let mut auto_escalated = false;
    if chat_mode == "chat" {
        if let Some(msg) = message_raw.as_deref() {
            if message_needs_tools(msg) {
                chat_mode = "agent".to_string();
                auto_escalated = true;
                crate::pylog::info(
                    "chat→agent auto-escalation: message matched tool-intent pattern",
                );
            }
        }
    }

    let active_doc_id = f.get("active_doc_id").cloned().unwrap_or_default();
    let active_doc_id = active_doc_id.trim().to_string();
    crate::pylog::info(&format!(
        "[doc-inject] chat_mode={chat_mode}, active_doc_id={active_doc_id:?}"
    ));

    // coerce_message_and_session: attachment-only sends allow an empty message.
    let has_atts = attachments.as_deref().map(|a| !a.is_empty()).unwrap_or(false);
    let message = match message_raw.clone() {
        Some(m) if !m.trim().is_empty() => {
            // `validate_message`: after stripping, reject when `len(message) > 50000`.
            // Python counts CHARACTERS (Python `len` on a `str`), so use `chars()`,
            // not bytes. This is only reached on the non-`allow_empty` path, exactly
            // as `coerce_message_and_session` calls `validate_message`.
            let trimmed = m.trim().to_string();
            if trimmed.chars().count() > 50000 {
                return Err(HttpException::new(400, "Message exceeds maximum length"));
            }
            trimmed
        }
        // `allow_empty` (attachment-only): `coerce_message_and_session` normalizes
        // ANY missing/empty/whitespace message to `""` and skips `validate_message`,
        // so this must come before BOTH empty-message error arms below.
        _ if has_atts => String::new(),
        // `validate_message` distinguishes two empty cases (src/chat_helpers.py:26-37):
        //   * `if not message:` — a MISSING (None) or EMPTY ("") message is falsy in
        //     Python, so it raises "Message is required";
        //   * a PRESENT, non-empty message that `.strip()`s to "" (whitespace-only) is
        //     truthy, so it PASSES the first check, then `len(message) == 0` after the
        //     strip raises "Message cannot be empty".
        // The first match arm already consumed every present-and-non-whitespace
        // message, so here `Some(s)` with `s` non-empty is necessarily whitespace-only
        // (truthy in Python → "Message cannot be empty"), while `Some("")`/`None` is
        // the falsy missing/empty case (→ "Message is required").
        Some(s) if !s.is_empty() => {
            return Err(HttpException::new(400, "Message cannot be empty"))
        }
        _ => return Err(HttpException::new(400, "Message is required")),
    };
    let session = match session_raw {
        Some(sid) if !sid.is_empty() => sid,
        _ => return Err(HttpException::new(400, "Session ID is required")),
    };

    // `_verify_session_owner(request, session)` AFTER coerce, BEFORE load.
    auth_adapter::verify_session_owner(&s, user.as_deref(), &session)?;
    let mut sess = s
        .sessions
        .get_session(&session)
        .map_err(|_| HttpException::new(404, format!("Session '{session}' not found")))?;

    // `if _clear_orphaned_session_endpoint(sess, owner=owner): raise HTTPException(400, ...)`
    // — the session points at a deleted endpoint; clear it and bail (otherwise we'd
    // POST a dead endpoint and surface a generic 401/503).
    if clear_orphaned_session_endpoint(&mut sess, user.as_deref()) {
        return Err(HttpException::new(
            400,
            "Selected model endpoint was removed. Pick another model in Settings.",
        ));
    }

    // Issue #587: the picker shows a model from the endpoint cache but s.model never
    // made it onto the DB row (first-send race after endpoint setup, or a previous
    // endpoint delete/recreate). Pull the first cached model off the matching
    // endpoint so the upstream isn't called with model="" (which surfaces as a
    // generic 401/503).
    recover_empty_session_model(&mut sess, &session, user.as_deref());
    if sess.model.trim().is_empty() {
        return Err(HttpException::new(
            400,
            "No model selected for this chat. Open the model picker and choose one before sending.",
        ));
    }

    // Privilege gates BEFORE any LLM work / token spend.
    chat_helpers::enforce_chat_privileges(&s.auth, user.as_deref(), &sess.model)?;

    // Ensure session has auth headers (resolve_session_auth). The ported
    // SessionManager already hydrates headers from the endpoint on load; the
    // Python `resolve_session_auth` re-resolves only when the session has no
    // authorization/x-api-key header. The hydration is equivalent here.
    resolve_session_auth(&mut sess, &session);

    // Check for research_pending BEFORE the mode persist overwrites it.
    let mut do_research = use_research.to_lowercase() == "true";
    if !do_research {
        if let Ok(conn) = session_local() {
            let db_mode: Option<String> = conn
                .query_row(
                    "SELECT mode FROM sessions WHERE id = ?1",
                    rusqlite::params![session],
                    |r| r.get::<_, Option<String>>(0),
                )
                .ok()
                .flatten();
            if db_mode.as_deref() == Some("research_pending") {
                do_research = true;
                crate::pylog::info(&format!(
                    "Session {session} in research_pending — auto-triggering research"
                ));
            }
        }
    }

    // Persist session mode (research > agent > chat).
    let mut effective_mode = if do_research {
        "research".to_string()
    } else if chat_mode.is_empty() {
        "chat".to_string()
    } else {
        chat_mode.clone()
    };
    if matches!(effective_mode.as_str(), "agent" | "research" | "chat") {
        if let Ok(conn) = session_local() {
            if let Err(e) = conn.execute(
                "UPDATE sessions SET mode = ?1 WHERE id = ?2",
                rusqlite::params![effective_mode, session],
            ) {
                crate::pylog::warning(&format!("Failed to persist session mode: {e}"));
            }
        }
    }

    // att_ids — from the `attachments` form field (JSON list string).
    let att_ids: Vec<String> = match attachments.as_deref() {
        Some(a) if !a.is_empty() => match serde_json::from_str::<Vec<Value>>(a) {
            Ok(list) => list
                .into_iter()
                .map(|v| match v {
                    Value::String(s) => s,
                    other => other.to_string(),
                })
                .collect(),
            Err(_) => Vec::new(),
        },
        _ => Vec::new(),
    };

    let no_memory = form_truthy(&f, "no_memory");

    // Build shared context (stream path uses enhanced_message for the preface).
    let mut ctx = chat_helpers::build_chat_context(
        &mut sess,
        user.as_deref(),
        &s.chat_handler,
        &s.chat_processor,
        &message,
        &session,
        BuildContextOpts {
            preset_id: preset_id.as_deref(),
            att_ids: Some(&att_ids),
            use_web: Some(use_web),
            use_rag: use_rag.as_deref(),
            time_filter: time_filter.as_deref(),
            incognito,
            no_memory,
            search_context: search_context.as_deref(),
            compare_mode,
            webhook_manager: Some(&s.webhook_manager),
            use_enhanced_message: true,
            // Skills index only ships in agent mode.
            agent_mode: chat_mode == "agent",
            ..Default::default()
        },
    )
    .await?;

    // Query active document — prefer the explicit frontend ID, fall back to the
    // session lookup, then the in-memory active-doc id (orphaned-doc rescue). All
    // three lookups are OWNER-SCOPED (`_owner_session_filter(q, ctx.user)`) to fix
    // the cross-user document-injection bug — the explicit-id path previously looked
    // up by id alone, so a user could inject another user's document by passing its
    // id (chat_routes.py:497-546).
    let active_doc = resolve_active_document(&active_doc_id, &session, ctx.user.as_deref());

    // Build disabled-tools set from the frontend toggles + user privileges.
    let mut disabled_tools: HashSet<String> = HashSet::new();
    if !allow_bash {
        disabled_tools.insert("bash".to_string());
    }
    if !allow_web_search {
        disabled_tools.insert("web_search".to_string());
        disabled_tools.insert("web_fetch".to_string());
    }

    // Nobody/incognito mode: deny identity-linked tools.
    if incognito {
        for t in ["manage_memory", "search_chats", "manage_skills"] {
            disabled_tools.insert(t.to_string());
        }
    }

    // Enforce per-user privileges.
    let mut research_allowed = true;
    let user_for_privs = ctx.user.clone();
    if let Some(u) = user_for_privs.as_deref().filter(|u| !u.is_empty()) {
        let privs = s.auth.get_privileges(u);
        let pf = |k: &str| privs.get(k).and_then(|v| v.as_bool()).unwrap_or(true);
        if !pf("can_use_bash") {
            for t in ["bash", "python", "read_file", "write_file"] {
                disabled_tools.insert(t.to_string());
            }
        }
        if !pf("can_use_browser") {
            disabled_tools.insert("builtin_browser".to_string());
        }
        if !pf("can_use_documents") {
            for t in ["create_document", "edit_document", "update_document", "suggest_document"] {
                disabled_tools.insert(t.to_string());
            }
        }
        if !pf("can_generate_images") {
            disabled_tools.insert("generate_image".to_string());
        }
        if !pf("can_manage_memory") {
            disabled_tools.insert("manage_memory".to_string());
            disabled_tools.insert("manage_skills".to_string());
        }
        if !pf("can_use_research") {
            research_allowed = false;
        }
        if !pf("can_use_agent") {
            effective_mode = "chat".to_string();
            chat_mode = "chat".to_string();
        }
    }

    // Global admin disabled tools.
    if let Value::Array(globals) = crate::src::settings::get_setting("disabled_tools", json!([])) {
        for t in globals {
            if let Value::String(name) = t {
                disabled_tools.insert(name);
            }
        }
    }

    // Light auto-escalation: withhold the heavy "do things on the computer" tools.
    if auto_escalated {
        for t in ["bash", "python", "read_file", "write_file", "builtin_browser"] {
            disabled_tools.insert(t.to_string());
        }
    }

    // Disable document tools in compare sessions ([CMP] prefix).
    if sess.name.starts_with("[CMP]") {
        for t in ["create_document", "edit_document", "update_document"] {
            disabled_tools.insert(t.to_string());
        }
    }

    // Compare mode: strip the comparison-breaking tools.
    if compare_mode {
        for t in [
            "create_document", "edit_document", "update_document", "chat_with_model",
            "create_session", "list_sessions", "send_to_session", "pipeline",
            "manage_session", "manage_memory", "list_models", "generate_image", "ui_control",
        ] {
            disabled_tools.insert(t.to_string());
        }
        if chat_mode == "chat" {
            for t in [
                "bash", "python", "read_file", "write_file", "web_search",
                "web_fetch", "search_chats", "manage_tasks",
            ] {
                disabled_tools.insert(t.to_string());
            }
        }
    }

    // ── Odysseus-Rust codex/agent dispatch (folded into the union — see module
    // docstring) ──
    let endpoint_url = sess.endpoint_url.clone();
    // Mode A (`codex:`) — the codex app-server harness; never `is_agent`.
    let is_codex = crate::core::codex::is_codex_url(&endpoint_url);
    // Mode B (`codex-responses:`) — routed through stream_llm (which dispatches to
    // stream_codex_responses); participates in `is_agent` since it is not `is_codex`.
    let is_codex_responses = crate::core::codex::is_codex_responses_url(&endpoint_url);
    // `is_agent` = the Python `chat_mode == "agent"` (and never for Mode A, which is
    // already agentic server-side).
    let is_agent = !is_codex && chat_mode == "agent";

    // Snapshot the values the detached stream task needs to own (it must be 'static).
    let sessions = s.sessions.clone();
    let memory_manager = s.memory_manager.clone();
    let memory_vector = s.memory_vector.clone();
    let webhook_manager = s.webhook_manager.clone();
    let skills_manager = s.skills_manager.clone();
    let research_handler = s.research_handler.clone();
    let owner = ctx.user.clone();
    let model = sess.model.clone();
    let headers = sess.headers.clone();
    let preset_id_owned = preset_id.clone();
    let session_owned = session.clone();

    // Take the per-context lists the stream owns (ChatContext is consumed below).
    let preset_temp = ctx.preset.temperature.unwrap_or(0.3);
    let preset_max = ctx.preset.max_tokens.unwrap_or(0);
    let preset_char = ctx.preset.character_name.clone();
    let context_length = ctx.context_length;
    let was_compacted = ctx.was_compacted;
    let rag_sources = std::mem::take(&mut ctx.rag_sources);
    let mut web_sources = std::mem::take(&mut ctx.web_sources);
    let used_memories = std::mem::take(&mut ctx.used_memories);
    let auto_opened_docs = std::mem::take(&mut ctx.auto_opened_docs);
    let attachment_meta = ctx.preprocessed.attachment_meta.clone();
    let messages = std::mem::take(&mut ctx.messages);
    let uprefs = ctx.uprefs.clone();
    // Mirror the just-added user message into the in-memory session cache.
    // `get_session` returns a CLONE (we can't hold the cache lock across `.await`),
    // unlike Python where it yields the LIVE cached object that `add_message`
    // mutates in place. Without mirroring, `session_manager.sessions[id].history`
    // stays empty and the cache-only readers `fork_session` / `peek_session`
    // (which do NOT hydrate from the DB) copy 0 messages. Doing it here also keeps
    // the user message in the cache if the client disconnects mid-stream (the
    // partial-save guard then appends the assistant).
    sessions.with_session_mut(&session_owned, |cached| {
        cached.history = sess.history.clone();
        cached.message_count = sess.message_count;
    });
    // sess is moved into the stream for save_assistant_response (`&mut Session`).
    let mut sess_owned = sess;

    let do_research_effective = do_research && research_allowed;

    // ── The detached SSE generator (stream_with_save) ──
    let stream = async_stream::stream! {
        let mut full_response = String::new();
        let mut last_metrics: Option<Value> = None;
        // `research_sources` stays `None` on the chat/agent paths (the research
        // branch returns before any save), matching the Python's function-scoped
        // default — it is only ever read by `save_assistant_response`.
        let research_sources: Option<Vec<Value>> = None;

        // Register active stream for the partial-save safety net.
        stream_register(&session_owned, json!({
            "status": "streaming", "partial": "", "query": message,
            "is_research": do_research, "mode": effective_mode,
        }));

        // attachments event.
        if !attachment_meta.is_empty() {
            yield sse_bytes(&format!("data: {}\n\n",
                json!({"type": "attachments", "data": attachment_meta})));
        }

        // Auto-opened docs (e.g. fillable PDF → markdown).
        for opened in &auto_opened_docs {
            let mut obj = match opened.clone() {
                Value::Object(m) => m,
                _ => Map::new(),
            };
            obj.insert("type".to_string(), json!("doc_update"));
            yield sse_bytes(&format!("data: {}\n\n", Value::Object(obj)));
        }

        if !rag_sources.is_empty() {
            yield sse_bytes(&format!("data: {}\n\n",
                json!({"type": "rag_sources", "data": rag_sources})));
        }
        if !web_sources.is_empty() {
            yield sse_bytes(&format!("data: {}\n\n",
                json!({"type": "web_sources", "data": web_sources})));
        }
        if !used_memories.is_empty() {
            yield sse_bytes(&format!("data: {}\n\n",
                json!({"type": "memories_used", "data": used_memories})));
        }

        let mut messages = messages;

        // ── Research branch ──
        if do_research && do_research_effective {
            let (r_ep, r_model, r_headers) = resolve_research_endpoint(&sess_owned);

            // Clarification round: only for short/vague queries on first research
            // message (skip in compare mode).
            let prior_json = read_research_json(&research_handler, &session_owned);
            let history_len = sess_owned.history.len();
            let is_first_research = prior_json.is_none() && history_len <= 2 && !compare_mode;

            let skip_research = if is_first_research {
                crate::pylog::info(&format!(
                    "First research message — asking clarifying questions for: {}",
                    char_prefix(&message, 60)
                ));
                yield sse_bytes(&format!("data: {}\n\n",
                    json!({"type": "model_info", "model": model, "suffix": "Research"})));
                // Set DB mode to research_pending so the NEXT message auto-triggers.
                if let Ok(conn) = session_local() {
                    let _ = conn.execute(
                        "UPDATE sessions SET mode = ?1 WHERE id = ?2",
                        rusqlite::params!["research_pending", session_owned],
                    );
                }
                insert_at(&mut messages, 0, json!({"role": "system", "content":
                    "The user wants to start deep web research. Before searching, ask 2-3 brief \
                     clarifying questions to understand exactly what they want to know. For example: \
                     what aspects matter most, are they comparing to something, what's their context \
                     (moving, traveling, curiosity). Be conversational. Keep it short."}));
                true
            } else {
                false
            };

            if !skip_research {
                // Phase 2: start the actual research.
                // Check for prior research to continue from.
                let mut prior_report = String::new();
                let mut prior_findings: Vec<Value> = Vec::new();
                let mut prior_urls: Vec<String> = Vec::new();
                if let Some(pj) = &prior_json {
                    prior_report = pj
                        .get("raw_report")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string();
                    if let Some(f) = pj.get("raw_findings").and_then(Value::as_array) {
                        prior_findings = f.clone();
                    }
                    if let Some(srcs) = pj.get("sources").and_then(Value::as_array) {
                        let urls: Vec<String> = srcs
                            .iter()
                            .filter_map(|src| src.get("url").and_then(Value::as_str))
                            .filter(|u| !u.is_empty())
                            .map(str::to_string)
                            .collect();
                        prior_urls = urls;
                        if !prior_report.is_empty() {
                            crate::pylog::info(&format!(
                                "Continuing research for session {} with {} prior URLs",
                                session_owned, prior_urls.len()
                            ));
                        }
                    }
                }

                // Synthesize the conversation into a focused research query.
                let research_query = research_handler
                    .synthesize_query(&sess_owned, &message, &r_ep, &r_model, r_headers.clone())
                    .await;
                crate::pylog::info(&format!("Research query: {}", char_prefix(&research_query, 120)));

                // `on_complete` — persist the research result to DB when the
                // background task finishes. Mirrors `_on_research_done` (skips on
                // incognito; logs + swallows errors).
                let on_complete = build_research_on_complete(
                    sessions.clone(), session_owned.clone(), incognito,
                );

                research_handler.start_research(crate::src::research_handler::StartResearchArgs {
                    session_id: session_owned.clone(),
                    query: research_query,
                    llm_endpoint: r_ep.clone(),
                    llm_model: r_model.clone(),
                    llm_headers: r_headers.clone(),
                    on_complete: Some(on_complete),
                    prior_report,
                    prior_findings,
                    prior_urls,
                    owner: owner.clone().unwrap_or_default(),
                    ..Default::default()
                });

                // Poll loop: emit research_progress + heartbeats until the run leaves
                // "running".
                let mut heartbeat_counter = 0i64;
                let mut last_progress: Value = json!({});
                let mut sent_avg = false;
                loop {
                    let status = research_handler.get_status(&session_owned);
                    let status = match status {
                        Some(st) if st.get("status").and_then(Value::as_str) == Some("running") => st,
                        _ => break,
                    };
                    let progress = status.get("progress").cloned().unwrap_or_else(|| json!({}));
                    let progress_nonempty = progress
                        .as_object()
                        .map(|o| !o.is_empty())
                        .unwrap_or(false);
                    if progress_nonempty && progress != last_progress {
                        last_progress = progress.clone();
                        let mut prog = match progress.clone() {
                            Value::Object(m) => m,
                            _ => Map::new(),
                        };
                        if !sent_avg {
                            sent_avg = true;
                            if let Some(sa) = status.get("started_at") {
                                prog.insert("started_at".to_string(), sa.clone());
                            }
                            // Python reads `avg = status.get("avg_duration")` (already
                            // `round(avg, 1)`-rounded inside `get_status`) and only
                            // attaches it when truthy. Read the rounded value off the
                            // status — `get_avg_duration()` returns the raw float, so
                            // reading the status keeps the 1-decimal rounding Python
                            // emits (`if avg:` skips a 0/absent value).
                            if let Some(avg) = status.get("avg_duration") {
                                let avg_truthy = avg.as_f64().map(|n| n != 0.0).unwrap_or(false);
                                if avg_truthy {
                                    prog.insert("avg_duration".to_string(), avg.clone());
                                }
                            }
                        }
                        yield sse_bytes(&format!("data: {}\n\n",
                            json!({"type": "research_progress", "data": Value::Object(prog)})));
                        heartbeat_counter = 0;
                    } else {
                        heartbeat_counter += 1;
                        yield sse_bytes(&format!(": heartbeat {heartbeat_counter}\n\n"));
                    }
                    tokio::time::sleep(std::time::Duration::from_millis(1000)).await;
                }

                let srcs = research_handler.get_sources(&session_owned);
                if let Some(srcs) = &srcs {
                    if !srcs.is_empty() {
                        yield sse_bytes(&format!("data: {}\n\n",
                            json!({"type": "research_sources", "data": srcs})));
                    }
                }
                // (Python assigns `research_sources` here, but the research branch
                // returns right below — the assignment is only ever read in the
                // chat/agent save paths, which never run after this. Bound to `_`.)
                let _ = srcs;

                if let Some(findings) = research_handler.get_raw_findings(&session_owned) {
                    if !findings.is_empty() {
                        yield sse_bytes(&format!("data: {}\n\n",
                            json!({"type": "research_findings", "data": findings})));
                    }
                }

                yield sse_bytes(&format!("data: {}\n\n",
                    json!({"type": "research_done", "data": {"session_id": session_owned}})));
                yield sse_bytes("data: [DONE]\n\n");
                research_handler.clear_result(&session_owned);
                stream_set(&session_owned, &[("status", json!("done"))]);
                stream_pop(&session_owned);
                return;
            }
        }

        // Auto-compact notification.
        if was_compacted {
            yield sse_bytes(&format!("data: {}\n\n",
                json!({"type": "compacted", "context_length": context_length})));
        }

        // Configured fallback chain for the default chat model — tried in order if
        // the session's primary model fails before producing output. Resolved once
        // per request. LIVE: `resolve_chat_fallback_candidates()` is ported
        // (chat_routes.py:624-628). The Python wraps it in `try/except: []`, but the
        // Rust fn cannot panic and returns `[]` when unconfigured, so the `except`
        // branch is moot — drop the try wrapper, keep the same unconfigured outcome.
        let fallback_candidates: Vec<(String, String, indexmap::IndexMap<String, String>)> =
            crate::src::endpoint_resolver::resolve_chat_fallback_candidates();

        // Send the model name early (model_info).
        let mut model_info = json!({"type": "model_info", "model": model});
        if let Some(obj) = model_info.as_object_mut() {
            if do_research {
                obj.insert("suffix".to_string(), json!("Research"));
            }
            if let Some(cn) = preset_char.as_deref().filter(|c| !c.is_empty()) {
                obj.insert("character_name".to_string(), json!(cn));
            }
        }
        yield sse_bytes(&format!("data: {model_info}\n\n"));

        // Whether this chat session should bypass text chat and generate images
        // (`_is_image_generation_session(sess, owner=_user)`, owner-scoped).
        let is_image_model = is_image_generation_session(&model, &endpoint_url, owner.as_deref());

        if is_image_model {
            // Route image-capable models straight to image generation
            // (chat_routes.py:660-692). The `do_generate_image` port lives in
            // `src/ai_interaction.rs`; the crate has no cargo feature flags, so
            // both it and this branch are always compiled and the call is always
            // in scope here.
            //
            // `if not get_setting("image_gen_enabled", True):` — admin kill switch.
            if !crate::src::settings::get_setting("image_gen_enabled", json!(true)).as_bool().unwrap_or(true) {
                yield sse_bytes(&format!("data: {}\n\n",
                    json!({"delta": "Image generation is disabled by the administrator."})));
                yield sse_bytes("data: [DONE]\n\n");
                stream_pop(&session_owned);
                return;
            }
            // `_user_msg = message or ""` — Python uses the FULL, untruncated message
            // for the do_generate_image content; only the SSE `command` field is the
            // `[:100]` truncation. `message` is a `String` here (never None), so the
            // `or ""` is moot.
            let user_msg = &message;
            // `command` = `_user_msg[:100]` (char-wise) — reused for tool_start,
            // tool_output, and the tool_events record.
            let command = char_prefix(user_msg, 100);
            yield sse_bytes(&format!("data: {}\n\n",
                json!({"type": "tool_start", "tool": "generate_image", "command": command})));
            yield sse_bytes(": heartbeat\n\n");

            // `await do_generate_image(f"{_user_msg}\n{sess.model}", session)` — Python
            // passes only the session id positionally, so `owner` defaults to None.
            let img_result = crate::src::ai_interaction::do_generate_image(
                &format!("{user_msg}\n{model}"),
                Some(session_owned.as_str()),
                None,
            )
            .await;

            // `_img_output = _img_result.get("results", _img_result.get("error", ""))`
            // — results first, else error, else "".
            let has_error = img_result.contains_key("error");
            let img_output = img_result
                .get("results")
                .or_else(|| img_result.get("error"))
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            // `exit_code = 0 if "error" not in _img_result else 1`.
            let exit_code = if has_error { 1 } else { 0 };

            // `_img_tool_data = {type, tool, command, output, exit_code}` then copy the
            // six image_* keys when PRESENT (`if _k in _img_result`).
            let mut tool_data = Map::new();
            tool_data.insert("type".to_string(), json!("tool_output"));
            tool_data.insert("tool".to_string(), json!("generate_image"));
            tool_data.insert("command".to_string(), json!(command));
            tool_data.insert("output".to_string(), json!(img_output));
            tool_data.insert("exit_code".to_string(), json!(exit_code));
            for k in ["image_url", "image_id", "image_prompt", "image_model", "image_size", "image_quality"] {
                if let Some(v) = img_result.get(k) {
                    tool_data.insert(k.to_string(), v.clone());
                }
            }
            yield sse_bytes(&format!("data: {}\n\n", Value::Object(tool_data)));

            // `_desc = _img_result.get("results", _img_result.get("error", "Image generation complete"))`.
            let desc = img_result
                .get("results")
                .or_else(|| img_result.get("error"))
                .and_then(Value::as_str)
                .unwrap_or("Image generation complete")
                .to_string();
            // `full_response = _desc` (Python L679; consumed by the save below).
            full_response = desc.clone();
            yield sse_bytes(&format!("data: {}\n\n", json!({"delta": desc})));

            // Save to session history (Python L682-688) — gated on `not incognito`.
            if !incognito {
                // `_ev = {round:1, tool, command, output, exit_code}` then copy the six
                // image_* keys when TRUTHY (`if _img_result.get(_ek):` — distinct from
                // the tool_output copy above, which uses `in`).
                let mut ev = Map::new();
                ev.insert("round".to_string(), json!(1));
                ev.insert("tool".to_string(), json!("generate_image"));
                ev.insert("command".to_string(), json!(command));
                ev.insert("output".to_string(), json!(img_output));
                ev.insert("exit_code".to_string(), json!(exit_code));
                for k in ["image_url", "image_id", "image_prompt", "image_model", "image_size", "image_quality"] {
                    if let Some(v) = img_result.get(k) {
                        // Python truthiness: skip null/false/0/""/empty-collection.
                        let truthy = match v {
                            Value::Null => false,
                            Value::Bool(b) => *b,
                            Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
                            Value::String(s) => !s.is_empty(),
                            Value::Array(a) => !a.is_empty(),
                            Value::Object(o) => !o.is_empty(),
                        };
                        if truthy {
                            ev.insert(k.to_string(), v.clone());
                        }
                    }
                }
                // `sess.add_message(ChatMessage("assistant", full_response,
                //     metadata={"tool_events": [_ev], "model": sess.model}))` then
                // `session_manager.save_sessions()`. Mirror the manual
                // `with_session_mut(history.push + message_count) + _persist_message`
                // idiom used by the partial-save guard (this is `Session.add_message`,
                // NOT the chat/agent `save_assistant_response` path).
                let mut metadata = Map::new();
                metadata.insert("tool_events".to_string(), json!([Value::Object(ev)]));
                metadata.insert("model".to_string(), json!(model));
                let msg = ChatMessage::new("assistant", full_response.clone(), Some(metadata));
                sessions.with_session_mut(&session_owned, |s| {
                    s.history.push(msg.clone());
                    s.message_count = s.history.len() as i64;
                });
                use crate::core::models::SessionPersistence as _;
                sessions._persist_message(&session_owned, &msg);
            }

            yield sse_bytes(&format!("data: {}\n\n",
                json!({"type": "metrics", "data": {"total_time": 0}})));
            yield sse_bytes("data: [DONE]\n\n");
            stream_pop(&session_owned);
            return;
        }

        // The url handed to the HTTP-provider paths: for BOTH codex schemes pass the
        // RAW scheme url through untouched (build_chat_url would mangle
        // `codex-responses:`); for ordinary endpoints build the chat-completions url.
        let chat_url = if is_codex_responses || is_codex {
            endpoint_url.clone()
        } else {
            crate::src::endpoint_resolver::build_chat_url(&endpoint_url)
        };

        // ── Build the inner provider stream (Mode A / agent / chat) ──
        let inner: std::pin::Pin<Box<dyn futures_util::Stream<Item = String> + Send>> = if is_codex {
            // Mode A: the codex app-server harness (codex runs its OWN tools).
            let home = crate::core::codex::codex_home(&endpoint_url);
            let cwd = crate::pyos::getenv("ODYSSEUS_CODEX_CWD", "/tmp");
            Box::pin(crate::core::codex::stream_chat(
                session_owned.clone(), home, Some(model.clone()), messages.clone(), cwd,
            ))
        } else if is_agent {
            // Agent mode: the round-based tool loop. Mode B rides this path (its raw
            // `codex-responses:` url flows to stream_llm → stream_codex_responses).
            let args = crate::src::agent_loop::AgentLoopArgs {
                endpoint_url: chat_url.clone(),
                model: model.clone(),
                messages: messages.clone(),
                headers: headers.clone(),
                temperature: preset_temp,
                max_tokens: preset_max,
                prompt_type: preset_id_owned.clone(),
                max_tool_calls: crate::src::settings::get_setting("agent_max_tool_calls", json!(0))
                    .as_i64().unwrap_or(0),
                context_length,
                active_document: active_doc.clone(),
                session_id: Some(session_owned.clone()),
                disabled_tools: if disabled_tools.is_empty() { None } else { Some(disabled_tools.clone()) },
                owner: owner.clone(),
                fallbacks: if fallback_candidates.is_empty() { None } else { Some(fallback_candidates.clone()) },
                ..Default::default()
            };
            Box::pin(crate::src::agent_loop::stream_agent_loop(args))
        } else {
            // Chat mode: stream_llm_with_fallback (primary + the resolved fallbacks).
            let mut cands = vec![(chat_url.clone(), model.clone(), headers.clone())];
            cands.extend(fallback_candidates.clone());
            Box::pin(crate::src::llm_core::stream_llm_with_fallback(
                cands, messages.clone(), preset_temp, preset_max, preset_id_owned.clone(), None, 300,
            ))
        };

        // ── The shared relay loop: accumulate deltas, convert usage→metrics, forward
        // tool/agent events, persist + post-tasks on [DONE] ──
        let chat_start = crate::pytime::time();
        let est_input = crate::src::model_context::estimate_tokens(&messages);
        let mut agent_rounds: i64 = 0;
        let mut agent_tool_calls: i64 = 0;
        let mut sent_metrics = false;
        // `_answered_by = None` — set if the selected model failed and a fallback
        // answered (the `type:"fallback"` event below). Used so metrics record the
        // model that ACTUALLY answered, not the masked selected model
        // (chat_routes.py:833/946 + the `_answered_by or sess.model` reads).
        let mut answered_by: Option<String> = None;

        // Partial-save safety net (the Python cancel handlers around BOTH relay
        // loops). The shared buffer mirrors `full_response` so the guard's `Drop`
        // can persist whatever accumulated if the stream is dropped/cancelled
        // before the `[DONE]` save. Disarmed on the normal `[DONE]` path below.
        let partial_buf = std::sync::Arc::new(std::sync::Mutex::new(String::new()));
        let mut partial_guard = PartialSaveGuard::new(
            partial_buf.clone(),
            sessions.clone(),
            session_owned.clone(),
            model.clone(),
        );

        futures_util::pin_mut!(inner);
        while let Some(chunk) = inner.next().await {
            if chunk.starts_with("data: ") && !chunk.starts_with("data: [DONE]") {
                let body = &chunk[6..];
                match serde_json::from_str::<Value>(body.trim_end()) {
                    Ok(data) => {
                        if let Some(delta) = data.get("delta").and_then(Value::as_str) {
                            // Reasoning tokens arrive flagged `thinking:true`. Forward
                            // them so the client can show a thinking indicator, but do
                            // NOT fold them into the saved reply / partial buffer
                            // (chat_routes.py:853-861 chat-mode, 970-977 agent-mode).
                            // Both Python relay loops gate the accumulation on
                            // `if not data.get("thinking"):` — this single union loop
                            // mirrors both.
                            let is_thinking = data
                                .get("thinking")
                                .and_then(Value::as_bool)
                                .unwrap_or(false);
                            if !is_thinking {
                                full_response.push_str(delta);
                                // Mirror the delta into the shared buffer the partial-save
                                // guard reads on Drop.
                                if let Ok(mut buf) = partial_buf.lock() {
                                    buf.push_str(delta);
                                }
                                stream_set(&session_owned, &[("partial", json!(full_response))]);
                            }
                            yield sse_bytes(&chunk);
                        } else if data.get("type").and_then(Value::as_str) == Some("web_sources") {
                            // Agent mode emits its own web_sources.
                            web_sources = data.get("data").and_then(Value::as_array).cloned().unwrap_or_default();
                            yield sse_bytes(&chunk);
                        } else if data.get("type").and_then(Value::as_str) == Some("fallback") {
                            // Selected model failed; a fallback answered. Forward the
                            // notice and remember the real model so metrics reflect it,
                            // not the masked selected model (chat_routes.py:862-866
                            // chat-mode, 991-997 agent-mode). `_answered_by =
                            // data.get("answered_by") or _answered_by` — keep the prior
                            // value when the event carries no (or an empty) answered_by.
                            if let Some(ab) = data
                                .get("answered_by")
                                .and_then(Value::as_str)
                                .filter(|s| !s.is_empty())
                            {
                                answered_by = Some(ab.to_string());
                            }
                            yield sse_bytes(&chunk);
                        } else if data.get("type").and_then(Value::as_str) == Some("usage") {
                            let mut m = data.get("data").and_then(Value::as_object).cloned().unwrap_or_default();
                            // `last_metrics["model"] = _answered_by or sess.model`.
                            m.insert(
                                "model".to_string(),
                                json!(answered_by.clone().unwrap_or_else(|| model.clone())),
                            );
                            if context_length > 0 {
                                if let Some(it) = m.get("input_tokens").and_then(Value::as_i64) {
                                    let pct = ((it as f64 / context_length as f64) * 100.0 * 10.0).round() / 10.0;
                                    let pct = pct.min(100.0);
                                    m.insert("context_percent".to_string(), json!(pct));
                                    m.insert("context_length".to_string(), json!(context_length));
                                }
                            }
                            // Real-usage metrics (chat_routes.py:874-882): the frontend
                            // reads `tokens_per_second`; the raw usage event carries the
                            // backend's true gen speed as `gen_tps` (llama.cpp timings).
                            // `if last_metrics.get("gen_tps") and not last_metrics.get("tokens_per_second"):`
                            //   `last_metrics["tokens_per_second"] = last_metrics["gen_tps"]`
                            //   `last_metrics["tps_source"] = "backend"`
                            // Both sides use Python truthiness — a missing/null/0 value
                            // is falsy — so map only when gen_tps is truthy AND
                            // tokens_per_second is falsy.
                            let value_truthy = |v: &Value| -> bool {
                                match v {
                                    Value::Null => false,
                                    Value::Bool(b) => *b,
                                    Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
                                    Value::String(s) => !s.is_empty(),
                                    Value::Array(a) => !a.is_empty(),
                                    Value::Object(o) => !o.is_empty(),
                                }
                            };
                            let gen_tps_truthy =
                                m.get("gen_tps").map(value_truthy).unwrap_or(false);
                            let tps_truthy = m
                                .get("tokens_per_second")
                                .map(value_truthy)
                                .unwrap_or(false);
                            if gen_tps_truthy && !tps_truthy {
                                if let Some(g) = m.get("gen_tps").cloned() {
                                    m.insert("tokens_per_second".to_string(), g);
                                    m.insert("tps_source".to_string(), json!("backend"));
                                }
                            }
                            // Wall-clock response time for the stats popup ("Time").
                            // `last_metrics.setdefault("response_time", round(time()-_chat_start, 2))`
                            // — only set when absent.
                            if !m.contains_key("response_time") {
                                let rt = ((crate::pytime::time() - chat_start) * 100.0).round() / 100.0;
                                m.insert("response_time".to_string(), json!(rt));
                            }
                            last_metrics = Some(Value::Object(m.clone()));
                            sent_metrics = true;
                            yield sse_bytes(&format!("data: {}\n\n",
                                json!({"type": "metrics", "data": Value::Object(m)})));
                        } else if data.get("type").and_then(Value::as_str) == Some("metrics") {
                            // Agent loop emits its own metrics event.
                            let mut m = data.get("data").and_then(Value::as_object).cloned().unwrap_or_default();
                            // `last_metrics["model"] = _answered_by or sess.model`.
                            m.insert(
                                "model".to_string(),
                                json!(answered_by.clone().unwrap_or_else(|| model.clone())),
                            );
                            last_metrics = Some(Value::Object(m.clone()));
                            sent_metrics = true;
                            yield sse_bytes(&format!("data: {}\n\n",
                                json!({"type": "metrics", "data": Value::Object(m)})));
                        } else {
                            // tool_start / tool_output / agent_step / doc_* / ui_control.
                            match data.get("type").and_then(Value::as_str) {
                                Some("agent_step") => {
                                    let r = data.get("round").and_then(Value::as_i64).unwrap_or(1);
                                    agent_rounds = agent_rounds.max(r);
                                }
                                Some("tool_start") => {
                                    agent_tool_calls += 1;
                                }
                                _ => {}
                            }
                            yield sse_bytes(&chunk);
                        }
                    }
                    Err(_) => yield sse_bytes(&chunk),
                }
            } else if chunk.starts_with("event: ") {
                yield sse_bytes(&chunk);
            } else if chunk == "data: [DONE]\n\n" {
                // Fallback `usage_source:"estimated"` metrics if the upstream sent no
                // usage. Python emits this ONLY inside the CHAT-MODE `[DONE]` handler
                // (`if not last_metrics and full_response`) — the agent-mode `[DONE]`
                // handler has no such fallback. This union folds both modes into one
                // relay loop, so gate the fallback to chat mode: `!is_agent` (and not
                // Mode A codex, which is agentic server-side and never enters the
                // chat-mode estimate path either).
                if !is_agent && !is_codex && !sent_metrics && !full_response.is_empty() {
                    let elapsed = crate::pytime::time() - chat_start;
                    let est_out = (full_response.chars().count() / 4) as i64;
                    let tps = if elapsed > 0.0 {
                        ((est_out as f64 / elapsed) * 100.0).round() / 100.0
                    } else { 0.0 };
                    let ctx_pct = if context_length > 0 {
                        (((est_input as f64 / context_length as f64) * 100.0 * 10.0).round() / 10.0).min(100.0)
                    } else { 0.0 };
                    let m = json!({
                        "response_time": (elapsed * 100.0).round() / 100.0,
                        "input_tokens": est_input,
                        "output_tokens": est_out,
                        "tokens_per_second": tps,
                        "context_percent": ctx_pct,
                        "context_length": context_length,
                        "model": model,
                        "usage_source": "estimated",
                    });
                    last_metrics = Some(m.clone());
                    yield sse_bytes(&format!("data: {}\n\n", json!({"type": "metrics", "data": m})));
                }
                if !full_response.is_empty() {
                    let saved_id = chat_helpers::save_assistant_response(
                        &mut sess_owned,
                        &sessions,
                        &session_owned,
                        &full_response,
                        last_metrics.as_ref(),
                        SaveAssistantOpts {
                            character_name: preset_char.as_deref(),
                            web_sources: Some(&web_sources),
                            rag_sources: Some(&rag_sources),
                            research_sources: research_sources.as_deref(),
                            used_memories: Some(&used_memories),
                            do_research,
                            incognito,
                            ..Default::default()
                        },
                    );
                    if let Some(id) = saved_id {
                        yield sse_bytes(&format!("data: {}\n\n",
                            json!({"type": "message_saved", "id": id})));
                    }
                    chat_helpers::run_post_response_tasks(
                        &sess_owned,
                        sessions.clone(),
                        &session_owned,
                        &message,
                        &full_response,
                        last_metrics.as_ref(),
                        &uprefs,
                        memory_manager.clone(),
                        memory_vector.clone(),
                        Some(webhook_manager.clone()),
                        PostResponseOpts {
                            incognito,
                            compare_mode,
                            character_name: preset_char.as_deref(),
                            agent_rounds,
                            agent_tool_calls,
                            skills_manager: if is_agent { Some(&skills_manager) } else { None },
                            owner: owner.as_deref(),
                            extract_skills: user_requested_agent,
                        },
                    );
                }
                // Mirror the completed turn (user + assistant + any tool/agent
                // messages added to `sess_owned`) back into the in-memory cache —
                // see the user-message mirror at the route level. Without this,
                // `fork_session` / `peek_session` (cache-only, no DB hydration) copy
                // 0 messages after a streamed turn.
                sessions.with_session_mut(&session_owned, |cached| {
                    cached.history = sess_owned.history.clone();
                    cached.message_count = sess_owned.message_count;
                });
                stream_set(&session_owned, &[("status", json!("done"))]);
                yield sse_bytes(&chunk);
            } else {
                yield sse_bytes(&chunk);
            }
        }

        // ── Disarm the partial-save guard now that the relay loop has exited for
        // ANY reason ──
        //
        // The Python's `except (asyncio.CancelledError, GeneratorExit):` partial-save
        // (chat_routes.py:774-781 chat-mode, 861-877 agent-mode) fires ONLY on a
        // genuine mid-loop cancellation — the client disconnected / the run was
        // Stopped while the `async for chunk in ...` was SUSPENDED inside the loop.
        // It does NOT fire on a normal loop completion, regardless of HOW the loop
        // ended:
        //   * via `data: [DONE]` (the `[DONE]` handler already saved the response);
        //   * via `event: error` then a normal end — `stream_llm` emits `event: error`
        //     and returns WITHOUT a `[DONE]` on a provider error, after which this
        //     relay loop simply runs out of items and exits NORMALLY (the stream is
        //     exhausted). Python only saves the partial on CancelledError/GeneratorExit,
        //     NEVER on a provider error, so this must NOT save here;
        //   * via plain exhaustion (the inner stream ended without `[DONE]`).
        //
        // In Rust a dropped `async_stream` future just stops being polled — there is
        // no catchable cancel signal — so the ONLY way to leave the guard armed is for
        // this `stream!` future to be dropped while still suspended INSIDE the loop
        // above (true cancellation). Disarming HERE, immediately after the loop exits
        // for any reason, guarantees that a normal completion — `[DONE]`, the
        // error-then-end path, or plain exhaustion — never leaves the guard armed, so
        // `Drop` only ever saves on a real mid-stream cancellation. This single relay
        // loop is the union of the Python chat-mode AND agent-mode loops, so both are
        // covered by this one disarm.
        partial_guard.disarm();

        // The `_safe_stream` / mode-specific `finally`: guarantee cleanup.
        stream_pop(&session_owned);
    };

    // Run the stream as a DETACHED background task (it survives the client closing
    // the tab); the SSE response just subscribes (replay + live). Reconnect via
    // /api/chat/resume.
    //
    // agent_runs::start wraps a `Stream<Item = String>`, so adapt the
    // `Result<Bytes, Infallible>` stream back to plain strings for the run buffer
    // (subscribe re-wraps them as the SSE body).
    let string_stream = stream.map(|r: Result<bytes::Bytes, std::convert::Infallible>| {
        String::from_utf8_lossy(&r.unwrap_or_default()).into_owned()
    });
    crate::src::agent_runs::start(&session, string_stream);
    let body = crate::src::agent_runs::subscribe(&session).map(sse_string);

    Ok(Response::builder()
        .header(header::CONTENT_TYPE, "text/event-stream")
        .header(header::CACHE_CONTROL, "no-cache")
        .header("X-Accel-Buffering", "no")
        .body(Body::from_stream(body))
        .unwrap())
}

// ===========================================================================
// GET /api/chat/resume/:session_id — reconnect to a detached run still going
// ===========================================================================

/// `GET /api/chat/resume/{session_id}` — re-subscribe to a still-running detached run.
async fn chat_resume(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(session_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    auth_adapter::verify_session_owner(&s, user.as_deref(), &session_id)?;
    // `if not agent_runs.is_active(session_id): raise HTTPException(404, ...)`
    if !crate::src::agent_runs::is_active(&session_id) {
        return Err(HttpException::new(404, "No active run for this session"));
    }
    let body = crate::src::agent_runs::subscribe(&session_id).map(sse_string);
    Ok(Response::builder()
        .header(header::CONTENT_TYPE, "text/event-stream")
        .header(header::CACHE_CONTROL, "no-cache")
        .header("X-Accel-Buffering", "no")
        .body(Body::from_stream(body))
        .unwrap())
}

// ===========================================================================
// POST /api/chat/stop/:session_id — cancel a detached run (the Stop button)
// ===========================================================================

/// `POST /api/chat/stop/{session_id}` — cancel a detached run.
async fn chat_stop(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(session_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    auth_adapter::verify_session_owner(&s, user.as_deref(), &session_id)?;
    let stopped = crate::src::agent_runs::stop(&session_id);
    Ok(Json(json!({ "stopped": stopped })).into_response())
}

// ===========================================================================
// GET /api/chat/stream_status/:session_id — is a stream active for this session?
// ===========================================================================

/// `GET /api/chat/stream_status/{session_id}` — report stream status. A detached
/// run can still be going even after `_active_streams` was popped, so report it as
/// active so the client knows to reconnect via /resume.
async fn chat_stream_status(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(session_id): Path<String>,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    auth_adapter::verify_session_owner(&s, user.as_deref(), &session_id)?;
    match stream_get(&session_id) {
        Some(entry) => Ok(Json(entry).into_response()),
        None => {
            if crate::src::agent_runs::is_active(&session_id) {
                Ok(Json(json!({"status": "streaming", "detached": true})).into_response())
            } else {
                Err(HttpException::new(404, "No active stream for this session"))
            }
        }
    }
}

// ===========================================================================
// POST /api/inject_context/:session_id
// ===========================================================================

/// `POST /api/inject_context/{session_id}` — inject a research-context message into
/// the session history.
async fn inject_context(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    Path(session_id): Path<String>,
    mp: Multipart,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    auth_adapter::verify_session_owner(&s, user.as_deref(), &session_id)?;
    let f = parse_form(mp).await;
    // `context: str = Form(...)` — required.
    let context = f
        .get("context")
        .cloned()
        .ok_or_else(|| HttpException::new(422, "Unprocessable Entity"))?;

    match s.sessions.get_session(&session_id) {
        Ok(mut sess) => {
            let msg = crate::src::prompt_security::untrusted_context_message(
                "injected research context",
                Some(&format!("Research Context: {context}")),
            );
            let role = msg.get("role").and_then(Value::as_str).unwrap_or("system").to_string();
            let content = msg.get("content").and_then(Value::as_str).unwrap_or("").to_string();
            let metadata = msg.get("metadata").and_then(Value::as_object).cloned();
            sess.add_message(ChatMessage::new(role, content, metadata));
            s.sessions.save_sessions();
            Ok(Json(json!({"status": "context_injected"})).into_response())
        }
        Err(_) => Err(HttpException::new(404, "Session not found")),
    }
}

// ===========================================================================
// GET /api/search — search across chat messages
// ===========================================================================

#[derive(serde::Deserialize)]
struct SearchQuery {
    #[serde(default)]
    q: String,
    #[serde(default = "default_limit")]
    limit: i64,
}

fn default_limit() -> i64 {
    20
}

/// `GET /api/search` — full-text-ish search across `chat_messages` (owner-scoped
/// when a user resolves). Mirrors the SQLAlchemy `query(ChatMessage, Session.name)
/// .join(Session).filter(...).order_by(timestamp desc).limit(limit)`.
async fn search_messages(
    user: Option<Extension<CurrentUser>>,
    Query(q): Query<SearchQuery>,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    // `if not q or not q.strip(): return []`
    let query_term = q.q.trim().to_string();
    if query_term.is_empty() {
        return Ok(Json(json!([])).into_response());
    }
    // `limit: int = Query(20, ge=1, le=100)`.
    let limit = q.limit.clamp(1, 100);

    let conn = session_local().map_err(|e| HttpException::new(500, e.to_string()))?;
    let like = format!("%{query_term}%");

    // Build the query (owner filter only when a user resolves).
    let sql = if user.as_deref().filter(|u| !u.is_empty()).is_some() {
        "SELECT cm.session_id, s.name, cm.role, cm.content, cm.timestamp \
         FROM chat_messages cm JOIN sessions s ON cm.session_id = s.id \
         WHERE s.archived = 0 AND cm.content LIKE ?1 \
           AND cm.role IN ('user', 'assistant') AND s.owner = ?3 \
         ORDER BY cm.timestamp DESC LIMIT ?2"
    } else {
        "SELECT cm.session_id, s.name, cm.role, cm.content, cm.timestamp \
         FROM chat_messages cm JOIN sessions s ON cm.session_id = s.id \
         WHERE s.archived = 0 AND cm.content LIKE ?1 \
           AND cm.role IN ('user', 'assistant') \
         ORDER BY cm.timestamp DESC LIMIT ?2"
    };

    let mut stmt = conn
        .prepare(sql)
        .map_err(|e| HttpException::new(500, e.to_string()))?;

    let map_row = |row: &rusqlite::Row| -> rusqlite::Result<SearchRow> {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, Option<String>>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, Option<String>>(3)?.unwrap_or_default(),
            row.get::<_, Option<String>>(4)?,
        ))
    };
    let rows: Vec<SearchRow> =
        if let Some(u) = user.as_deref().filter(|u| !u.is_empty()) {
            stmt.query_map(rusqlite::params![like, limit, u], map_row)
                .and_then(|r| r.collect())
                .map_err(|e| HttpException::new(500, e.to_string()))?
        } else {
            stmt.query_map(rusqlite::params![like, limit], map_row)
                .and_then(|r| r.collect())
                .map_err(|e| HttpException::new(500, e.to_string()))?
        };

    let lower_term = query_term.to_lowercase();
    let mut results: Vec<Value> = Vec::new();
    for (session_id, session_name, role, content, timestamp) in rows {
        // Snippet around the first case-insensitive match (char-based slicing to
        // mirror Python str indexing).
        let snippet = build_snippet(&content, &lower_term, query_term.chars().count());
        results.push(json!({
            "session_id": session_id,
            "session_name": session_name.filter(|n| !n.is_empty()).unwrap_or_else(|| "Untitled".to_string()),
            "role": role,
            "content_snippet": snippet,
            "timestamp": timestamp,
        }));
    }
    Ok(Json(Value::Array(results)).into_response())
}

// ===========================================================================
// POST /api/rewrite — lightweight rewrite of the last AI message (no tools)
// ===========================================================================

/// `POST /api/rewrite` — rewrite the last AI message per an instruction. Does NOT
/// run the agent loop or any tools; just asks the LLM to rewrite the given text.
async fn rewrite_message(
    State(s): State<AppState>,
    user: Option<Extension<CurrentUser>>,
    body: axum::body::Bytes,
) -> Result<Response, HttpException> {
    let user = current_user(&user);
    // `body = await request.json()` (400 on invalid JSON).
    let body: Value =
        serde_json::from_slice(&body).map_err(|_| HttpException::new(400, "Invalid JSON"))?;

    let session_id = body.get("session_id").and_then(Value::as_str).unwrap_or("").to_string();
    let original_text = body.get("original_text").and_then(Value::as_str).unwrap_or("").to_string();
    let instruction = body.get("instruction").and_then(Value::as_str).unwrap_or("").to_string();

    if session_id.is_empty() || original_text.is_empty() || instruction.is_empty() {
        return Err(HttpException::new(
            400,
            "session_id, original_text, and instruction are required",
        ));
    }

    auth_adapter::verify_session_owner(&s, user.as_deref(), &session_id)?;

    let sess = s
        .sessions
        .get_session(&session_id)
        .map_err(|_| HttpException::new(404, "Session not found"))?;

    let messages = vec![
        json!({"role": "system", "content":
            "You are rewriting a previous response. Follow the instruction exactly. \
             Output ONLY the rewritten text — no preamble, no explanation, no meta-commentary. \
             Preserve any formatting (markdown, code blocks, lists) from the original."}),
        json!({"role": "user", "content":
            format!("Here is the original response:\n\n{original_text}\n\nInstruction: {instruction}")}),
    ];

    let endpoint_url = sess.endpoint_url.clone();
    let model = sess.model.clone();
    let headers = sess.headers.clone();
    let sessions = s.sessions.clone();
    let sid = session_id.clone();

    let stream = async_stream::stream! {
        let mut full_response = String::new();
        // `max_tokens=0` (let the server decide), `temperature=0.7`, `tools=None`.
        let inner = crate::src::llm_core::stream_llm(
            endpoint_url, model, messages, 0.7, 0, headers, 300, None, None,
        );
        futures_util::pin_mut!(inner);
        while let Some(chunk) = inner.next().await {
            if chunk.starts_with("data: ") && !chunk.starts_with("data: [DONE]") {
                let body = &chunk[6..];
                match serde_json::from_str::<Value>(body.trim_end()) {
                    Ok(data) => {
                        if let Some(delta) = data.get("delta").and_then(Value::as_str) {
                            // Don't fold reasoning tokens into the saved rewrite —
                            // only real content (reasoning arrives `thinking:true`).
                            if !data.get("thinking").and_then(Value::as_bool).unwrap_or(false) {
                                full_response.push_str(delta);
                            }
                            yield sse_bytes(&chunk);
                        }
                    }
                    Err(_) => yield sse_bytes(&chunk),
                }
            } else if chunk.starts_with("event: ") {
                yield sse_bytes(&chunk);
            } else if chunk == "data: [DONE]\n\n" {
                // Strip <think> blocks so the persisted rewrite is just the text.
                let stripped = crate::src::research_utils::strip_thinking(Some(&full_response))
                    .unwrap_or_default();
                let stripped = stripped.trim();
                let final_text = if stripped.is_empty() { full_response.clone() } else { stripped.to_string() };
                if !final_text.is_empty() {
                    // Python (chat_routes.py:1080-1106) mutates BOTH the LIVE session
                    // history AND the DB row, then `save_sessions()`. Mutate the live
                    // cached session first: walk `reversed(sess.history)` and set the
                    // content of the most-recent assistant message. `get_session`
                    // returns a CLONE, so this must go through `with_session_mut` to
                    // reach the cached object (otherwise the cache stays stale until a
                    // DB reload). The Rust history is always `ChatMessage` (no dict
                    // union), so just the `msg.role == "assistant"` branch applies.
                    sessions.with_session_mut(&sid, |s| {
                        if let Some(msg) = s.history.iter_mut().rev()
                            .find(|m| m.role == "assistant")
                        {
                            msg.content = final_text.clone();
                        }
                    });
                    // Update the last assistant message in DB too.
                    if let Ok(conn) = session_local() {
                        let _ = conn.execute(
                            // Python orders by `DBChatMessage.timestamp.desc()`
                            // (chat_routes.py:1270). The chat_messages table has no
                            // `created_at` column (only `timestamp`), so the old
                            // ORDER BY created_at silently matched no row on SQLite
                            // strict parsing / wrong column — order by `timestamp`.
                            "UPDATE chat_messages SET content = ?1 \
                             WHERE id = (SELECT id FROM chat_messages \
                                         WHERE session_id = ?2 AND role = 'assistant' \
                                         ORDER BY timestamp DESC LIMIT 1)",
                            rusqlite::params![final_text, sid],
                        );
                    }
                    sessions.save_sessions();
                }
                yield sse_bytes(&chunk);
            } else {
                yield sse_bytes(&chunk);
            }
        }
    };

    Ok(Response::builder()
        .header(header::CONTENT_TYPE, "text/event-stream")
        .header(header::CACHE_CONTROL, "no-cache")
        .header("X-Accel-Buffering", "no")
        .body(Body::from_stream(stream))
        .unwrap())
}

// ===========================================================================
// Internal helpers
// ===========================================================================

/// `String -> Ok(Bytes)` for an `agent_runs::subscribe` chunk (already a full SSE
/// frame string).
fn sse_string(s: String) -> Result<bytes::Bytes, std::convert::Infallible> {
    Ok(bytes::Bytes::from(s))
}

/// `text[:n]` by characters (Python `message[:n]`).
fn char_prefix(text: &str, n: usize) -> String {
    text.chars().take(n).collect()
}

/// `resolve_session_auth(sess, session_id)` — ensure the session has auth headers,
/// resolving from the endpoint DB when missing.
///
/// `has_auth = any(k.lower() in ('authorization','x-api-key') for k in sess.headers)`.
/// When present, no-op. Otherwise resolve from the first `model_endpoints` row whose
/// `base_url` contains the session's endpoint domain, persisting the resolved
/// headers back onto the session row.
fn resolve_session_auth(sess: &mut crate::core::models::Session, session_id: &str) {
    let has_auth = sess
        .headers
        .keys()
        .any(|k| {
            let lk = k.to_lowercase();
            lk == "authorization" || lk == "x-api-key"
        });
    if has_auth {
        return;
    }
    let url = &sess.endpoint_url;
    let domain = if url.contains("//") {
        url.split("//").nth(1).unwrap_or("").split('/').next().unwrap_or("").to_string()
    } else {
        String::new()
    };
    if domain.is_empty() {
        return;
    }
    let conn = match session_local() {
        Ok(c) => c,
        Err(_) => return,
    };
    let like = format!("%{domain}%");
    let row: Option<(Option<String>, String)> = conn
        .query_row(
            "SELECT api_key, base_url FROM model_endpoints WHERE base_url LIKE ?1 LIMIT 1",
            rusqlite::params![like],
            |r| Ok((r.get::<_, Option<String>>(0)?, r.get::<_, String>(1)?)),
        )
        .ok();
    if let Some((Some(api_key), base_url)) = row {
        if !api_key.is_empty() {
            let new_headers =
                crate::src::endpoint_resolver::build_headers(Some(&api_key), &base_url);
            sess.headers = new_headers.clone();
            let headers_map: serde_json::Map<String, Value> = new_headers
                .iter()
                .map(|(k, v)| (k.clone(), Value::String(v.clone())))
                .collect();
            let headers_json =
                serde_json::to_string(&Value::Object(headers_map)).unwrap_or_else(|_| "{}".to_string());
            let _ = conn.execute(
                "UPDATE sessions SET headers = ?1 WHERE id = ?2",
                rusqlite::params![headers_json, session_id],
            );
            crate::pylog::info(&format!(
                "Resolved and persisted auth headers for session {session_id}"
            ));
        }
    }
}

/// Query the active document — explicit frontend ID first, then the session
/// `is_active` fallback, then the in-memory active-doc id (orphaned-doc rescue).
/// Mirrors the Python `_doc_db` lookup chain. Returns the [`ActiveDocument`] the
/// agent loop accepts.
///
/// OWNER SCOPING (cross-user document-injection fix, chat_routes.py:497-546): every
/// lookup is wrapped in `_owner_session_filter(q, owner)`. The explicit-id path
/// previously looked up by id alone, so any user could inject another user's
/// document by passing its id. `_owner_session_filter` returns `q.filter(False)`
/// (matches NOTHING) when `owner is None` — so an unauthenticated request never
/// resolves a document — else `q.filter(Document.owner == owner)`. We realize that
/// via [`crate::routes::document_helpers::owner_session_filter`] →
/// [`crate::routes::document_helpers::OwnerFilter`] (SQL `"0"` / `"owner = ?"`),
/// `AND`-ed into each query.
fn resolve_active_document(
    active_doc_id: &str,
    session: &str,
    owner: Option<&str>,
) -> Option<crate::src::agent_loop::ActiveDocument> {
    let conn = session_local().ok()?;

    // `_owner_session_filter(q, ctx.user)` — the owner predicate AND-ed into each
    // query: `"0"` (constant-false, `filter(False)`) when owner is None, else
    // `"owner = ?"` with the owner bound.
    let of = crate::routes::document_helpers::owner_session_filter(owner);
    let owner_sql = of.sql();
    let owner_param = of.param();

    let select = "SELECT id, current_content, title, language, session_id FROM documents WHERE ";
    let row_to_doc = |r: &rusqlite::Row| -> rusqlite::Result<DocRow> {
        Ok((
            r.get::<_, String>(0)?,
            r.get::<_, Option<String>>(1)?,
            r.get::<_, Option<String>>(2)?,
            r.get::<_, Option<String>>(3)?,
            r.get::<_, Option<String>>(4)?,
        ))
    };

    // 1. By explicit frontend ID (owner-scoped).
    let mut found: Option<DocRow> = None;
    if !active_doc_id.is_empty() {
        let sql = format!("{select} id = ?1 AND {owner_sql}");
        found = match owner_param {
            Some(o) => conn
                .query_row(&sql, rusqlite::params![active_doc_id, o], row_to_doc)
                .ok(),
            None => conn
                .query_row(&sql, rusqlite::params![active_doc_id], row_to_doc)
                .ok(),
        };
        if found.is_none() {
            crate::pylog::warning(&format!("[doc-inject] NOT FOUND by ID {active_doc_id}"));
        }
    }

    // 2. Session fallback (the most-recently-updated active doc for this session,
    // owner-scoped).
    if found.is_none() {
        let sql = format!(
            "{select} session_id = ?1 AND is_active = 1 AND {owner_sql} \
             ORDER BY updated_at DESC LIMIT 1"
        );
        found = match owner_param {
            Some(o) => conn
                .query_row(&sql, rusqlite::params![session, o], row_to_doc)
                .ok(),
            None => conn
                .query_row(&sql, rusqlite::params![session], row_to_doc)
                .ok(),
        };
    }

    // 3. In-memory active-doc id (orphaned-doc rescue) — owner-scoped, plus the
    // additional guard against leaking a doc that belongs to a DIFFERENT session.
    if found.is_none() {
        if let Some(mem_id) = crate::src::tool_implementations::documents::get_active_document() {
            let sql = format!("{select} id = ?1 AND {owner_sql}");
            let cand: Option<DocRow> = match owner_param {
                Some(o) => conn
                    .query_row(&sql, rusqlite::params![mem_id, o], row_to_doc)
                    .ok(),
                None => conn
                    .query_row(&sql, rusqlite::params![mem_id], row_to_doc)
                    .ok(),
            };
            if let Some(cand) = cand {
                let cand_session = cand.4.as_deref().unwrap_or("");
                if cand_session.is_empty() || cand_session == session {
                    found = Some(cand);
                }
            }
        }
    }

    found.map(|(id, current_content, title, language, _sid)| {
        crate::src::agent_loop::ActiveDocument {
            id,
            current_content,
            title,
            language,
        }
    })
}

/// `_IMAGE_MODEL_PREFIXES` — model-name prefixes that are explicit image models.
const IMAGE_MODEL_PREFIXES: [&str; 3] = ["gpt-image", "dall-e", "chatgpt-image"];

/// `_session_url_matches_endpoint(session_url, endpoint_base)` — does the session's
/// stored `endpoint_url` resolve to this endpoint's base, by EXACT URL? (chat_routes.py:62-72)
///
/// `if not session_url or not endpoint_base: return False`. Otherwise the session
/// url (trailing-slash-stripped) must be in the tight set `{base,
/// base + "/chat/completions", build_chat_url(base)}` (each trailing-slash-stripped)
/// OR `startswith(base + "/")`. This is NOT a loose `base in session_url` substring,
/// so two image endpoints sharing a host can't misroute each other's models. (A
/// local copy — the chat_helpers `session_url_matches_endpoint` is private to that
/// module; this one additionally keeps the Python `startswith(base + "/")` clause.)
fn session_url_matches_endpoint(session_url: &str, endpoint_base: &str) -> bool {
    if session_url.is_empty() || endpoint_base.is_empty() {
        return false;
    }
    // `sess = session_url.rstrip("/")`
    let sess = session_url.trim_end_matches('/');
    // `base = normalize_base(endpoint_base).rstrip("/")`
    let base = crate::src::endpoint_resolver::normalize_base(Some(endpoint_base));
    let base = base.trim_end_matches('/');
    // `variants = {base, base + "/chat/completions", build_chat_url(base).rstrip("/")}`
    let chat_url = crate::src::endpoint_resolver::build_chat_url(base);
    let chat_url = chat_url.trim_end_matches('/');
    // `return sess in variants or sess.startswith(base + "/")`
    sess == base
        || sess == format!("{base}/chat/completions")
        || sess == chat_url
        || sess.starts_with(&format!("{base}/"))
}

/// `_endpoint_cache_contains_model(endpoint, model)` — does a populated endpoint
/// model cache include `model`? (chat_routes.py:106-122)
///
/// Empty/malformed caches are treated as UNKNOWN (`True`) rather than a negative
/// match, so older image endpoints without cached models still work:
///   * `raw = endpoint.cached_models; if not raw: return True`
///   * a JSON parse failure → `return True`
///   * a non-list / empty list → `return True`
///   * otherwise membership of `model.strip()` in the stripped cache entries.
fn endpoint_cache_contains_model(raw_cached: Option<&str>, model: &str) -> bool {
    // `if not raw: return True`
    let raw = match raw_cached {
        Some(s) if !s.is_empty() => s,
        _ => return true,
    };
    // `models = json.loads(raw) ... except: return True`
    let models: Vec<Value> = match serde_json::from_str::<Vec<Value>>(raw) {
        Ok(m) => m,
        Err(_) => return true,
    };
    // `if not isinstance(models, list) or not models: return True`
    if models.is_empty() {
        return true;
    }
    // `wanted = (model or "").strip()`; `return wanted in {str(item).strip() for item in models}`
    let wanted = model.trim();
    models.iter().any(|item| {
        let s = match item {
            Value::String(s) => s.clone(),
            other => other.to_string(),
        };
        s.trim() == wanted
    })
}

/// `_is_image_generation_session(sess, owner=None)` — whether this chat session
/// should bypass text chat and generate images (chat_routes.py:125-160).
///
/// Model-name prefixes are explicit image models. Endpoint type is only used when
/// the current session endpoint actually MATCHES that image endpoint (exact URL,
/// [`session_url_matches_endpoint`]) AND a populated endpoint model cache includes
/// the selected model ([`endpoint_cache_contains_model`]). This prevents an image
/// endpoint on the same host from misrouting ordinary text models into the
/// image-generation path. The endpoint scan is OWNER-SCOPED (`owner = ?owner OR
/// owner IS NULL` when `owner` is `Some`) — the `owner_filter(q, ModelEndpoint,
/// owner)` the Python applies.
fn is_image_generation_session(model: &str, endpoint_url: &str, owner: Option<&str>) -> bool {
    // `model = (sess.model or "").strip()`
    let model = model.trim();
    // `if any(model.lower().startswith(prefix) for prefix in _IMAGE_MODEL_PREFIXES): return True`
    let lower = model.to_lowercase();
    if IMAGE_MODEL_PREFIXES.iter().any(|p| lower.starts_with(p)) {
        return true;
    }

    // `endpoint_url = (sess.endpoint_url or "").strip(); if not endpoint_url: return False`
    let endpoint_url = endpoint_url.trim();
    if endpoint_url.is_empty() {
        return false;
    }

    // `q = db.query(ModelEndpoint).filter(is_enabled == True)`
    // `if owner: q = owner_filter(q, ModelEndpoint, owner)` — own rows + null-owner.
    // Confine the rusqlite Connection to this synchronous scope (no DB handle ever
    // crosses an await; this fn is fully sync).
    let conn = match session_local() {
        Ok(c) => c,
        // Python wraps the body in `try/except: return False` (the finally closes the
        // session) — a failure to open the DB is the False outcome.
        Err(_) => return false,
    };
    let endpoints: Vec<(String, Option<String>, Option<String>)> = {
        let (sql, params): (&str, Vec<&dyn rusqlite::ToSql>) = match &owner {
            Some(o) => (
                "SELECT base_url, model_type, cached_models FROM model_endpoints \
                 WHERE is_enabled = 1 AND (owner = ?1 OR owner IS NULL)",
                vec![o],
            ),
            None => (
                "SELECT base_url, model_type, cached_models FROM model_endpoints \
                 WHERE is_enabled = 1",
                vec![],
            ),
        };
        let mut stmt = match conn.prepare(sql) {
            Ok(s) => s,
            Err(_) => return false,
        };
        let rows = stmt.query_map(params.as_slice(), |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, Option<String>>(1)?,
                r.get::<_, Option<String>>(2)?,
            ))
        });
        match rows {
            Ok(it) => it.filter_map(Result::ok).collect(),
            Err(_) => return false,
        }
    };

    for (base_url, model_type, cached_models) in &endpoints {
        // `if (ep.model_type or "llm") != "image": continue`
        let mt = model_type.as_deref().filter(|s| !s.is_empty()).unwrap_or("llm");
        if mt != "image" {
            continue;
        }
        // `if not _session_url_matches_endpoint(endpoint_url, ep.base_url or ""): continue`
        if !session_url_matches_endpoint(endpoint_url, base_url) {
            continue;
        }
        // `if _endpoint_cache_contains_model(endpoint, model): return True`
        if endpoint_cache_contains_model(cached_models.as_deref(), model) {
            return true;
        }
    }
    false
}

/// `_clear_orphaned_session_endpoint(sess, owner=None) -> bool` (chat_routes.py:75-103).
///
/// Clear a session's model/endpoint if its endpoint was deleted from
/// `model_endpoints`. Returns `True` (and clears `sess.endpoint_url`/`model`/
/// `headers` + persists the cleared DB row with `updated_at = utcnow()`) iff the
/// session HAS a stored `endpoint_url` that matches NO enabled (owner-scoped)
/// endpoint by exact URL. A session with no endpoint_url, or one still matching a
/// live endpoint, returns `False` (no-op). The owner scope is `owner = ?owner OR
/// owner IS NULL`. DB errors are swallowed (Python `try/except: rollback; return
/// False`).
fn clear_orphaned_session_endpoint(
    sess: &mut crate::core::models::Session,
    owner: Option<&str>,
) -> bool {
    // `if not getattr(sess, "endpoint_url", ""): return False`
    if sess.endpoint_url.is_empty() {
        return false;
    }
    let conn = match session_local() {
        Ok(c) => c,
        Err(_) => return false,
    };
    // Collect the enabled (owner-scoped) endpoint base_urls.
    let endpoints: Vec<String> = {
        let (sql, params): (&str, Vec<&dyn rusqlite::ToSql>) = match &owner {
            Some(o) => (
                "SELECT base_url FROM model_endpoints \
                 WHERE is_enabled = 1 AND (owner = ?1 OR owner IS NULL)",
                vec![o],
            ),
            None => ("SELECT base_url FROM model_endpoints WHERE is_enabled = 1", vec![]),
        };
        let mut stmt = match conn.prepare(sql) {
            Ok(s) => s,
            Err(_) => return false,
        };
        let rows = stmt.query_map(params.as_slice(), |r| r.get::<_, String>(0));
        match rows {
            Ok(it) => it.filter_map(Result::ok).collect(),
            Err(_) => return false,
        }
    };
    // `for ep in endpoints: if _session_url_matches_endpoint(...): return False`
    for base_url in &endpoints {
        if session_url_matches_endpoint(&sess.endpoint_url, base_url) {
            return false;
        }
    }
    // No matching endpoint — clear the DB row (model + endpoint_url + updated_at).
    let now = crate::pydatetime::utcnow_naive_iso();
    let _ = conn.execute(
        "UPDATE sessions SET endpoint_url = '', model = '', updated_at = ?1 WHERE id = ?2",
        rusqlite::params![now, sess.id],
    );
    // `sess.endpoint_url = ""; sess.model = ""; sess.headers = {}`
    sess.endpoint_url = String::new();
    sess.model = String::new();
    sess.headers = indexmap::IndexMap::new();
    true
}

/// `_recover_empty_session_model(sess, session_id, owner=None) -> bool`
/// (chat_routes.py:163-229).
///
/// Re-populate `sess.model` from the matching endpoint's cached models when the
/// session has none (Issue #587: the picker showed a model but the session row
/// never got `s.model` written). Prefers the endpoint whose base URL matches the
/// session (exact URL); takes the first VISIBLE cached model (cached merged minus
/// hidden); persists it onto the session row (`updated_at = utcnow()`) so the next
/// request/reconnect/reload picks up the same model. Returns `True` iff `sess.model`
/// was repaired. The endpoint scan is owner-scoped. DB errors are swallowed.
fn recover_empty_session_model(
    sess: &mut crate::core::models::Session,
    session_id: &str,
    owner: Option<&str>,
) -> bool {
    // `if getattr(sess, "model", None): return False` — already has a model.
    if !sess.model.is_empty() {
        return false;
    }
    // `if not getattr(sess, "endpoint_url", ""): return False` (implicit — the loop
    // only runs when endpoint_url is set; with no endpoint there is no `ep`).
    if sess.endpoint_url.is_empty() {
        return false;
    }
    let conn = match session_local() {
        Ok(c) => c,
        Err(_) => return false,
    };
    // Find the matching enabled (owner-scoped) endpoint's cached + hidden models.
    let endpoints: Vec<(String, Option<String>, Option<String>)> = {
        let (sql, params): (&str, Vec<&dyn rusqlite::ToSql>) = match &owner {
            Some(o) => (
                "SELECT base_url, cached_models, hidden_models FROM model_endpoints \
                 WHERE is_enabled = 1 AND (owner = ?1 OR owner IS NULL)",
                vec![o],
            ),
            None => (
                "SELECT base_url, cached_models, hidden_models FROM model_endpoints \
                 WHERE is_enabled = 1",
                vec![],
            ),
        };
        let mut stmt = match conn.prepare(sql) {
            Ok(s) => s,
            Err(_) => return false,
        };
        let rows = stmt.query_map(params.as_slice(), |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, Option<String>>(1)?,
                r.get::<_, Option<String>>(2)?,
            ))
        });
        match rows {
            Ok(it) => it.filter_map(Result::ok).collect(),
            Err(_) => return false,
        }
    };
    // `for cand in endpoints: if _session_url_matches_endpoint(...): ep = cand; break`
    let ep = endpoints
        .iter()
        .find(|(base_url, _, _)| session_url_matches_endpoint(&sess.endpoint_url, base_url));
    // `if not ep: return False`
    let (_base_url, cached_models, hidden_models) = match ep {
        Some(e) => e,
        None => return false,
    };
    // `cached = json.loads(ep.cached_models) ... except: cached = []; if not cached: return False`
    let cached: Vec<String> = match cached_models.as_deref().filter(|s| !s.is_empty()) {
        Some(raw) => match serde_json::from_str::<Vec<Value>>(raw) {
            Ok(list) => list
                .into_iter()
                .map(|v| match v {
                    Value::String(s) => s,
                    other => other.to_string(),
                })
                .collect(),
            Err(_) => return false,
        },
        None => return false,
    };
    if cached.is_empty() {
        return false;
    }
    // `visible = _visible_models(cached, ep.hidden_models)` — cached minus hidden
    // (the model_routes `_visible_models` is private; replicate its hidden-filter
    // here — no pinned list to merge, matching the Python 2-arg call). `if not
    // visible: return False`.
    let visible = recover_visible_models(&cached, hidden_models.as_deref());
    if visible.is_empty() {
        return false;
    }
    // `model = visible[0]; if not isinstance(model, str) or not model.strip(): return False`
    let model = visible[0].trim();
    if model.is_empty() {
        return false;
    }
    let model = model.to_string();
    // Persist (updated_at = utcnow()) so the next request/reconnect/reload reuses it.
    let now = crate::pydatetime::utcnow_naive_iso();
    let _ = conn.execute(
        "UPDATE sessions SET model = ?1, updated_at = ?2 WHERE id = ?3",
        rusqlite::params![model, now, session_id],
    );
    // `sess.model = model`
    crate::pylog::info(&format!(
        "Recovered empty session model for {session_id} — picked {model:?} from endpoint"
    ));
    sess.model = model;
    true
}

/// `_visible_models(cached_models, hidden_models)` — cached IDs minus the hidden
/// set, preserving cached order. The 2-arg form `_recover_empty_session_model`
/// calls (no `pinned_models`); the `model_routes::visible_models` is private to
/// that module, so this replicates its behavior for that 2-arg call: normalize the
/// cached list (`_normalize_model_ids`: trim, drop empty, dedup — there is no
/// pinned list to merge), then filter out the normalized hidden set. Python:
/// `merged = _merge_model_ids(_normalize_model_ids(cached), _normalize_model_ids(None))`;
/// `if not hidden_models: return merged`;
/// `hidden = set(_normalize_model_ids(hidden_models)); return [m for m in merged if m not in hidden]`.
fn recover_visible_models(cached: &[String], hidden_models: Option<&str>) -> Vec<String> {
    // `_normalize_model_ids(cached)` — trim, drop empty, dedup (order-preserving).
    let mut seen: HashSet<String> = HashSet::new();
    let merged: Vec<String> = cached
        .iter()
        .filter_map(|m| {
            let t = m.trim();
            if t.is_empty() || !seen.insert(t.to_string()) {
                None
            } else {
                Some(t.to_string())
            }
        })
        .collect();
    // `if not hidden_models: return merged`.
    let hidden_raw = match hidden_models.filter(|s| !s.is_empty()) {
        Some(h) => h,
        None => return merged,
    };
    // `hidden = set(_normalize_model_ids(hidden_models))` — same trim/parse. A
    // malformed hidden list normalizes to `[]`, so it filters nothing.
    let hidden: HashSet<String> = match serde_json::from_str::<Vec<Value>>(hidden_raw) {
        Ok(list) => list
            .into_iter()
            .filter_map(|v| {
                let s = match v {
                    Value::String(s) => s,
                    other => other.to_string(),
                };
                let t = s.trim();
                if t.is_empty() {
                    None
                } else {
                    Some(t.to_string())
                }
            })
            .collect(),
        Err(_) => HashSet::new(),
    };
    merged.into_iter().filter(|m| !hidden.contains(m)).collect()
}

/// `_get_session_json(session_id)` — load the saved research JSON for a session, if
/// it exists. Reads the on-disk JSON via the handler's `session_json_path` (the
/// public accessor; the private `_get_session_json` is not exposed).
fn read_research_json(
    handler: &crate::src::research_handler::ResearchHandler,
    session_id: &str,
) -> Option<Value> {
    let path = handler.session_json_path(session_id);
    let text = std::fs::read_to_string(&path).ok()?;
    serde_json::from_str(&text).ok()
}

/// `_on_research_done(sid, result, sources, findings)` — the research persistence
/// callback. Mirrors the Python `_on_research_done`: skip on incognito; load the
/// session; build the metadata (`research`/`model`/`research_sources`/
/// `research_findings`), `clean_thinking_for_save`, append the assistant message,
/// `save_sessions`; log + swallow errors.
fn build_research_on_complete(
    sessions: std::sync::Arc<crate::core::session_manager::SessionManager>,
    session_id: String,
    incognito: bool,
) -> crate::src::research_handler::OnComplete {
    std::sync::Arc::new(move |_sid: String, result: String, sources: Vec<Value>, findings: Vec<Value>| {
        if incognito {
            return;
        }
        // `_s = session_manager.get_session(_sid)`.
        let mut sess = match sessions.get_session(&session_id) {
            Ok(s) => s,
            Err(_) => {
                crate::pylog::warning(&format!(
                    "Session {session_id} expired before research completed"
                ));
                return;
            }
        };
        let mut md = Map::new();
        md.insert("research".to_string(), json!(true));
        md.insert("model".to_string(), json!(sess.model));
        if !sources.is_empty() {
            md.insert("research_sources".to_string(), Value::Array(sources));
        }
        if !findings.is_empty() {
            md.insert("research_findings".to_string(), Value::Array(findings));
        }
        let (clean_res, clean_md) = chat_helpers::clean_thinking_for_save(&result, Some(&md));
        sess.add_message(ChatMessage::new(
            "assistant",
            clean_res,
            if clean_md.is_empty() { None } else { Some(clean_md) },
        ));
        sessions.save_sessions();
        crate::pylog::info(&format!(
            "Research result persisted to DB for session {session_id}"
        ));
    })
}

/// Build a snippet around the first case-insensitive match of `lower_term` in
/// `content`, mirroring the Python `idx = content.lower().find(...)` ±50-char
/// window with `"..."` ellipses (char-based to match Python str slicing).
fn build_snippet(content: &str, lower_term: &str, term_len: usize) -> String {
    let chars: Vec<char> = content.chars().collect();
    let lower_chars: Vec<char> = content.to_lowercase().chars().collect();
    let lower_term_chars: Vec<char> = lower_term.chars().collect();

    // `idx = lower_content.find(query_term.lower())` — char index of first match.
    let idx = find_subsequence(&lower_chars, &lower_term_chars);
    match idx {
        None => chars.iter().take(120).collect(),
        Some(i) => {
            // `start = max(0, idx - 50); end = min(len, idx + len(term) + 50)`.
            let start = i.saturating_sub(50);
            let end = (i + term_len + 50).min(chars.len());
            let mut out = String::new();
            if start > 0 {
                out.push_str("...");
            }
            out.extend(chars[start..end].iter());
            if end < chars.len() {
                out.push_str("...");
            }
            out
        }
    }
}

/// First index where `needle` occurs in `haystack` (char slices).
fn find_subsequence(haystack: &[char], needle: &[char]) -> Option<usize> {
    if needle.is_empty() || needle.len() > haystack.len() {
        return None;
    }
    (0..=haystack.len() - needle.len()).find(|&i| haystack[i..i + needle.len()] == *needle)
}

// ===========================================================================
// Tests
// ===========================================================================

#[cfg(test)]
mod tests {
    use super::*;

    // ── `message_needs_tools` — the REWRITTEN action_intents.py patterns ──

    #[test]
    fn message_needs_tools_empty_is_false() {
        // `if not text: return False`
        assert!(!message_needs_tools(""));
    }

    #[test]
    fn message_needs_tools_action_question_calendar() {
        // _ACTION_QUESTION + _CALENDAR_ACTION ... _CALENDAR_THING
        assert!(message_needs_tools("Can you add an entry to my calendar?"));
        assert!(message_needs_tools("Could you schedule a meeting for tomorrow?"));
        assert!(message_needs_tools("Will you book an appointment?"));
    }

    #[test]
    fn message_needs_tools_please_calendar() {
        // _PLEASE + _CALENDAR_ACTION ... to/on/in/into/for ... calendar
        assert!(message_needs_tools("add lunch to my calendar"));
        assert!(message_needs_tools("please put the dentist on the calendar"));
        // _PLEASE + _CALENDAR_ACTION + (calendar) event/meeting/...
        assert!(message_needs_tools("create a meeting"));
        assert!(message_needs_tools("please schedule an appointment"));
        // bare "put ... on calendar"
        assert!(message_needs_tools("put the standup on calendar"));
    }

    #[test]
    fn message_needs_tools_notes_and_reminders() {
        assert!(message_needs_tools("remind me to call mom"));
        assert!(message_needs_tools("Can you add a note about the trip?"));
        assert!(message_needs_tools("please add a todo"));
        assert!(message_needs_tools("please take a note"));
        assert!(message_needs_tools("please jot this down into my todo list"));
        assert!(message_needs_tools("please set a reminder"));
        assert!(message_needs_tools("Could you set a reminder?"));
    }

    #[test]
    fn message_needs_tools_email() {
        assert!(message_needs_tools("Can you reply to the unread emails?"));
        assert!(message_needs_tools("please send an email to Bob"));
        assert!(message_needs_tools("please archive these emails"));
        assert!(message_needs_tools("send an email"));
        assert!(message_needs_tools("email Sarah"));
        assert!(message_needs_tools("check my inbox"));
        assert!(message_needs_tools("unread emails"));
    }

    #[test]
    fn message_needs_tools_ui_panels_and_toggles() {
        // _PLEASE + (open|show|bring up) ... _PANEL
        assert!(message_needs_tools("please open the calendar"));
        assert!(message_needs_tools("show me my notes"));
        assert!(message_needs_tools("bring up the gallery"));
        // disable/enable/turn on|off toggles
        assert!(message_needs_tools("disable the shell"));
        assert!(message_needs_tools("turn on incognito"));
        assert!(message_needs_tools("enable research"));
    }

    #[test]
    fn message_needs_tools_deep_research() {
        assert!(message_needs_tools("please research the history of jazz"));
        assert!(message_needs_tools("Can you deep dive into quantum computing?"));
        assert!(message_needs_tools("please investigate the outage"));
    }

    #[test]
    fn message_needs_tools_shell_intent() {
        assert!(message_needs_tools("ssh into the prod box"));
        assert!(message_needs_tools("ssh server1"));
        assert!(message_needs_tools("run the migration on staging"));
        assert!(message_needs_tools("can you execute the script"));
        // _PLEASE-gated shell verbs (imperative position).
        assert!(message_needs_tools("please restart nginx"));
        assert!(message_needs_tools("deploy the service"));
        // _ACTION_QUESTION-gated shell verbs.
        assert!(message_needs_tools("Can you tail the logfile?"));
        assert!(message_needs_tools("check if the service is running"));
    }

    #[test]
    fn message_needs_tools_shell_verbs_only_imperative_or_question() {
        // The REWRITE made shell verbs prefix-gated: an informational question or an
        // incidental use must NOT promote. (This is precisely what the rewrite fixed
        // vs the OLD bare-word \b(deploy|...|cat|...)\b\s+\S+ list.)
        assert!(!message_needs_tools("What does the grep command do?"));
        assert!(!message_needs_tools("My cat ate my homework"));
        assert!(!message_needs_tools("the build pipeline is interesting"));
        // A mid-sentence "install" without the please/can-you prefix must not match.
        assert!(!message_needs_tools("I wonder how they install solar panels"));
    }

    #[test]
    fn message_needs_tools_plain_chat_is_false() {
        assert!(!message_needs_tools("what is the capital of France?"));
        assert!(!message_needs_tools("tell me a joke"));
        assert!(!message_needs_tools("how does a transformer work?"));
    }

    // ── `endpoint_cache_contains_model` — populated-cache membership ──

    #[test]
    fn endpoint_cache_contains_model_unknown_when_empty_or_malformed() {
        // not raw -> True; malformed -> True; empty list -> True.
        assert!(endpoint_cache_contains_model(None, "gpt-image-1"));
        assert!(endpoint_cache_contains_model(Some(""), "gpt-image-1"));
        assert!(endpoint_cache_contains_model(Some("not json"), "gpt-image-1"));
        assert!(endpoint_cache_contains_model(Some("[]"), "gpt-image-1"));
    }

    #[test]
    fn endpoint_cache_contains_model_membership() {
        let raw = r#"["gpt-image-1", "dall-e-3"]"#;
        assert!(endpoint_cache_contains_model(Some(raw), "gpt-image-1"));
        // stripped comparison both sides.
        assert!(endpoint_cache_contains_model(Some(raw), "  dall-e-3  "));
        assert!(!endpoint_cache_contains_model(Some(raw), "gpt-4o"));
    }

    // ── `session_url_matches_endpoint` — exact-URL set + startswith ──

    #[test]
    fn session_url_matches_endpoint_exact_and_prefix() {
        let base = "https://api.example.com/v1";
        assert!(session_url_matches_endpoint(
            "https://api.example.com/v1/chat/completions",
            base
        ));
        assert!(session_url_matches_endpoint("https://api.example.com/v1", base));
        assert!(session_url_matches_endpoint("https://api.example.com/v1/", base));
        // startswith(base + "/")
        assert!(session_url_matches_endpoint(
            "https://api.example.com/v1/responses",
            base
        ));
        // empty inputs -> False
        assert!(!session_url_matches_endpoint("", base));
        assert!(!session_url_matches_endpoint("https://api.example.com/v1", ""));
        // a different host must not match
        assert!(!session_url_matches_endpoint(
            "https://evil.example.com/v1/chat/completions",
            base
        ));
    }

    #[test]
    fn recover_visible_models_filters_hidden() {
        let cached = vec!["a".to_string(), "b".to_string(), "c".to_string()];
        assert_eq!(recover_visible_models(&cached, None), cached);
        assert_eq!(
            recover_visible_models(&cached, Some(r#"["b"]"#)),
            vec!["a".to_string(), "c".to_string()]
        );
        // malformed hidden filters nothing.
        assert_eq!(recover_visible_models(&cached, Some("nope")), cached);
    }

    // ── DB-backed helpers (real temp DB; never the live data dir) ──

    // Shared process-global lock (NOT a per-module one) so these DB tests
    // serialize against the sibling `chat_helpers` DB tests too — both swap the
    // process-wide `DATABASE_URL`.
    use crate::core::database::DB_TEST_LOCK;

    struct TmpDb(std::path::PathBuf);
    impl Drop for TmpDb {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }

    fn fresh_temp_db(tag: &str) -> TmpDb {
        let dir = std::env::temp_dir();
        let unique = format!(
            "odysseus_chat_routes_{tag}_{}_{}.db",
            std::process::id(),
            uuid::Uuid::new_v4().simple()
        );
        let path = dir.join(unique);
        let _ = std::fs::remove_file(&path);
        std::env::set_var("DATABASE_URL", format!("sqlite:///{}", path.display()));
        crate::core::database::create_all().unwrap();
        TmpDb(path)
    }

    fn now_iso() -> String {
        crate::pydatetime::utcnow_naive_iso()
    }

    #[allow(clippy::too_many_arguments)]
    fn seed_endpoint(
        conn: &rusqlite::Connection,
        id: &str,
        base_url: &str,
        enabled: bool,
        owner: Option<&str>,
        model_type: &str,
        cached_models: Option<&str>,
        hidden_models: Option<&str>,
    ) {
        let ts = now_iso();
        conn.execute(
            "INSERT INTO model_endpoints \
             (id, name, base_url, api_key, is_enabled, model_type, owner, cached_models, hidden_models, created_at, updated_at) \
             VALUES (?1, 'ep', ?2, 'sk-x', ?3, ?4, ?5, ?6, ?7, ?8, ?8)",
            rusqlite::params![id, base_url, enabled as i64, model_type, owner, cached_models, hidden_models, ts],
        )
        .unwrap();
    }

    fn seed_session(
        conn: &rusqlite::Connection,
        id: &str,
        endpoint_url: &str,
        model: &str,
        owner: Option<&str>,
    ) {
        let ts = now_iso();
        conn.execute(
            "INSERT INTO sessions (id, name, endpoint_url, model, owner, headers, created_at, updated_at) \
             VALUES (?1, 'sess', ?2, ?3, ?4, '{}', ?5, ?5)",
            rusqlite::params![id, endpoint_url, model, owner, ts],
        )
        .unwrap();
    }

    #[allow(clippy::too_many_arguments)]
    fn seed_document(
        conn: &rusqlite::Connection,
        id: &str,
        session_id: Option<&str>,
        owner: Option<&str>,
        is_active: bool,
        content: &str,
    ) {
        let ts = now_iso();
        conn.execute(
            "INSERT INTO documents \
             (id, session_id, title, language, current_content, is_active, owner, created_at, updated_at) \
             VALUES (?1, ?2, 'Doc', 'markdown', ?3, ?4, ?5, ?6, ?6)",
            rusqlite::params![id, session_id, content, is_active as i64, owner, ts],
        )
        .unwrap();
    }

    fn sess_with(endpoint_url: &str, model: &str) -> crate::core::models::Session {
        crate::core::models::Session {
            id: "s1".to_string(),
            endpoint_url: endpoint_url.to_string(),
            model: model.to_string(),
            ..Default::default()
        }
    }

    #[test]
    fn is_image_generation_session_model_prefix() {
        let _g = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db("img_prefix");
        // Prefix match short-circuits before any DB hit.
        assert!(is_image_generation_session(
            "gpt-image-1",
            "https://api.example.com/v1",
            None
        ));
        assert!(is_image_generation_session(
            "DALL-E-3",
            "https://api.example.com/v1",
            None
        ));
        // No endpoint_url -> False (after the prefix miss).
        assert!(!is_image_generation_session("gpt-4o", "", None));
    }

    #[test]
    fn is_image_generation_session_image_endpoint_match_and_cache_gate() {
        let _g = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db("img_endpoint");
        {
            let conn = crate::core::database::session_local().unwrap();
            // An IMAGE endpoint whose cache contains the model -> routes to image.
            seed_endpoint(
                &conn,
                "img1",
                "https://img.example.com/v1",
                true,
                Some("alice"),
                "image",
                Some(r#"["sdxl", "flux"]"#),
                None,
            );
            // An LLM endpoint on the SAME host that should NOT misroute text models.
            seed_endpoint(
                &conn,
                "llm1",
                "https://text.example.com/v1",
                true,
                Some("alice"),
                "llm",
                Some(r#"["gpt-4o"]"#),
                None,
            );
        }
        // Session points at the image endpoint with a cached image model -> True.
        assert!(is_image_generation_session(
            "sdxl",
            "https://img.example.com/v1/chat/completions",
            Some("alice")
        ));
        // Session points at the image endpoint but the model is NOT in its cache
        // (populated, non-empty) -> False (the cache gate prevents misrouting).
        assert!(!is_image_generation_session(
            "gpt-4o",
            "https://img.example.com/v1/chat/completions",
            Some("alice")
        ));
        // The LLM endpoint never routes to image regardless of model.
        assert!(!is_image_generation_session(
            "gpt-4o",
            "https://text.example.com/v1/chat/completions",
            Some("alice")
        ));
        // Owner scoping: a DIFFERENT user can't see alice's owner-scoped image endpoint.
        assert!(!is_image_generation_session(
            "sdxl",
            "https://img.example.com/v1/chat/completions",
            Some("bob")
        ));
    }

    #[test]
    fn clear_orphaned_session_endpoint_clears_when_no_match() {
        let _g = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db("orphan");
        seed_session_and_endpoint_for_orphan();
        // The session points at an endpoint base that exists -> NOT orphaned.
        let mut sess = sess_with("https://api.example.com/v1/chat/completions", "gpt-4o");
        assert!(!clear_orphaned_session_endpoint(&mut sess, Some("alice")));
        assert_eq!(sess.model, "gpt-4o");

        // A session pointing at a DELETED endpoint -> orphaned: cleared + persisted.
        let mut orphan = crate::core::models::Session {
            id: "s_orphan".to_string(),
            endpoint_url: "https://deleted.example.com/v1/chat/completions".to_string(),
            model: "ghost".to_string(),
            ..Default::default()
        };
        // Seed the DB row so we can verify the persisted clear.
        {
            let conn = crate::core::database::session_local().unwrap();
            seed_session(
                &conn,
                "s_orphan",
                "https://deleted.example.com/v1/chat/completions",
                "ghost",
                Some("alice"),
            );
        }
        assert!(clear_orphaned_session_endpoint(&mut orphan, Some("alice")));
        assert_eq!(orphan.model, "");
        assert_eq!(orphan.endpoint_url, "");
        // Persisted to DB.
        {
            let conn = crate::core::database::session_local().unwrap();
            let (eu, m): (String, String) = conn
                .query_row(
                    "SELECT endpoint_url, model FROM sessions WHERE id = 's_orphan'",
                    [],
                    |r| Ok((r.get(0)?, r.get(1)?)),
                )
                .unwrap();
            assert_eq!(eu, "");
            assert_eq!(m, "");
        }

        // A session with no endpoint_url is a no-op (returns False).
        let mut no_ep = crate::core::models::Session::default();
        assert!(!clear_orphaned_session_endpoint(&mut no_ep, Some("alice")));
    }

    fn seed_session_and_endpoint_for_orphan() {
        let conn = crate::core::database::session_local().unwrap();
        seed_endpoint(
            &conn,
            "ep1",
            "https://api.example.com/v1",
            true,
            Some("alice"),
            "llm",
            Some(r#"["gpt-4o"]"#),
            None,
        );
    }

    #[test]
    fn recover_empty_session_model_picks_first_visible() {
        let _g = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db("recover");
        {
            let conn = crate::core::database::session_local().unwrap();
            seed_endpoint(
                &conn,
                "ep1",
                "https://api.example.com/v1",
                true,
                Some("alice"),
                "llm",
                Some(r#"["hidden-model", "gpt-4o", "gpt-4o-mini"]"#),
                Some(r#"["hidden-model"]"#),
            );
            seed_session(
                &conn,
                "s_empty",
                "https://api.example.com/v1/chat/completions",
                "",
                Some("alice"),
            );
        }
        // Empty model + live endpoint -> picks the first VISIBLE cached model
        // (hidden-model is filtered out, so gpt-4o is chosen) and persists it.
        let mut sess = crate::core::models::Session {
            id: "s_empty".to_string(),
            endpoint_url: "https://api.example.com/v1/chat/completions".to_string(),
            model: String::new(),
            ..Default::default()
        };
        assert!(recover_empty_session_model(&mut sess, "s_empty", Some("alice")));
        assert_eq!(sess.model, "gpt-4o");
        // Persisted.
        {
            let conn = crate::core::database::session_local().unwrap();
            let m: String = conn
                .query_row("SELECT model FROM sessions WHERE id = 's_empty'", [], |r| {
                    r.get(0)
                })
                .unwrap();
            assert_eq!(m, "gpt-4o");
        }

        // A session that already has a model is a no-op.
        let mut has_model = sess_with("https://api.example.com/v1/chat/completions", "gpt-4o");
        assert!(!recover_empty_session_model(&mut has_model, "s1", Some("alice")));

        // Owner mismatch: bob can't recover from alice's owner-scoped endpoint.
        let mut bob_sess = crate::core::models::Session {
            id: "s_bob".to_string(),
            endpoint_url: "https://api.example.com/v1/chat/completions".to_string(),
            model: String::new(),
            ..Default::default()
        };
        assert!(!recover_empty_session_model(&mut bob_sess, "s_bob", Some("bob")));
        assert_eq!(bob_sess.model, "");
    }

    #[test]
    fn resolve_active_document_owner_scoped() {
        let _g = DB_TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _db = fresh_temp_db("doc_owner");
        {
            let conn = crate::core::database::session_local().unwrap();
            // The documents.session_id FK -> sessions(id) is enforced (PRAGMA
            // foreign_keys=ON), so the parent session must exist first.
            seed_session(&conn, "sess1", "http://x", "m", Some("alice"));
            // alice's active doc bound to her session.
            seed_document(&conn, "doc_alice", Some("sess1"), Some("alice"), true, "ALICE BODY");
            // bob's doc — a DIFFERENT owner.
            seed_document(&conn, "doc_bob", Some("sess1"), Some("bob"), true, "BOB BODY");
        }
        // alice can resolve her own doc by explicit id.
        let got = resolve_active_document("doc_alice", "sess1", Some("alice"));
        assert!(got.is_some());
        assert_eq!(got.unwrap().current_content.as_deref(), Some("ALICE BODY"));

        // CROSS-USER FIX: alice passing BOB's doc id must never surface bob's
        // content. The explicit-id lookup is owner-scoped (misses doc_bob), then —
        // exactly like the Python's chained `if not active_doc:` fallbacks — the
        // session fallback returns alice's OWN active doc. So she gets ALICE BODY,
        // never BOB BODY.
        let cross = resolve_active_document("doc_bob", "sess1", Some("alice"))
            .expect("alice's own active doc via the session fallback");
        assert_eq!(cross.current_content.as_deref(), Some("ALICE BODY"));
        assert_ne!(cross.current_content.as_deref(), Some("BOB BODY"));

        // No owner (None) -> filter(False) -> nothing resolves even with a valid id.
        assert!(resolve_active_document("doc_alice", "sess1", None).is_none());

        // Session fallback (no explicit id) is also owner-scoped: bob sees only his.
        let bob_fallback = resolve_active_document("", "sess1", Some("bob"));
        assert_eq!(
            bob_fallback.unwrap().current_content.as_deref(),
            Some("BOB BODY")
        );
    }
}
