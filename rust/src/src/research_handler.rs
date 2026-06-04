// src/research_handler.rs  <- src/research_handler.py
//! Handler for research service integration with expandable UI support.
//!
//! Uses the IterResearch-style `DeepResearcher` (LLM-in-the-loop) as the primary
//! engine, falling back to the legacy `ResearchOrchestrator` or basic web search
//! if needed.
//!
//! Includes a task registry so research survives page refreshes and can be
//! cancelled.
//!
//! PORT_PARTIAL. The pure / fs / persistence layer is faithfully ported and is
//! the live value of this module: the task registry, the on-disk JSON store
//! (`<DATA_DIR>/deep_research/<session>.json`), `_extract_sources` /
//! `_extract_raw_findings`, `get_status` / `get_result` / `get_sources` /
//! `get_raw_findings`, `clear_result`, `get_avg_duration`, `hide_image` /
//! `unhide_all_images`, `_format_research_report`, and `get_report_html` (wired to
//! the ported `crate::src::visual_report::generate_visual_report`).
//!
//! DOCUMENTED DEVIATIONS / DEFERRALS:
//! - `RESEARCH_DATA_DIR`: Python hardcodes the source-relative `Path("data/deep_research")`.
//!   The Rust port resolves it under `crate::core::constants::DATA_DIR` (which
//!   honors `ODYSSEUS_DATA_DIR`), matching how the rest of the batch resolves data
//!   files. This is a deliberate, documented deviation from the literal Python path.
//! - `llm_call_async` `max_retries=1` kwarg is dropped everywhere (single-attempt
//!   deviation, shared across the batch). `headers=None` -> empty `IndexMap`.
//! - The legacy `research_engine.ResearchOrchestrator` is permanently absent (the
//!   module never existed in this tree; Python's `ImportError` branch is the live
//!   one), so `_legacy_engine` is permanently `None` and `_fallback_research` skips
//!   straight to `_handle_research_failure`.
//! - `_handle_research_failure` basic-search fallback is wired to the real
//!   `crate::src::search::comprehensive_web_search`: on a successful basic search
//!   it emits the "Research Failed - Basic Search Fallback" report; if the
//!   fallback search itself fails (panicked blocking task) it emits the
//!   "Complete Research Failure" shape with the real error string.
//! - `event_bus.fire_event("research_completed", ...)` is now wired to the ported
//!   `crate::src::event_bus::fire_event` (both modules are `web`-gated); an empty
//!   owner collapses to `None`, matching Python's `entry.get("owner") or None`.
//! - `start_research`'s `hard_timeout` is now settings-resolved (Python
//!   `hard_timeout: int = None`): unset resolves `research_run_timeout_seconds`
//!   (default 1800; `0` = unlimited, i.e. no `tokio::time::timeout`; otherwise
//!   clamped to `[60, 86400]`). A caller that pins a value bypasses the lookup.
//! - `call_research_service` resolves `extraction_timeout` /
//!   `extraction_concurrency` from `research_extraction_timeout_seconds` /
//!   `research_extraction_concurrency` (clamped `[15,3600]` / `[1,12]`) onto the
//!   `ResearcherConfig`. CONVERGENCE GAP: the ported
//!   `crate::src::deep_research::DeepResearcher` does not yet carry those two
//!   fields, so `build_researcher` cannot hand them to the engine (a one-line
//!   addition in deep_research.rs, out of scope for this single-file edit). The
//!   values are still resolved/clamped faithfully and ready to wire.
//! - The `DeepResearcher` engine itself (`src/deep_research.py`) is the ported
//!   sibling `crate::src::deep_research::DeepResearcher`, wired through the
//!   [`ResearchEngine`] trait seam (the [`DeepResearcherAdapter`]). The engine's
//!   `_search` / `_fetch_and_extract` are now wired to the real
//!   `crate::src::search` engine, so a research run uses live search and only
//!   degrades to the engine's own "Search unavailable" branch when search is
//!   genuinely down — never a fabricated search hit.
//!
//! Whole module is async/HTTP surface (HTTP via `llm_call_async`, tokio task registry).

use crate::core::constants::DATA_DIR;
use crate::error::{PyError, PyResult};
use crate::pylog as logger;
use crate::pyos as os;
use crate::pytime;
use crate::src::llm_core::llm_call_async;
use crate::src::research_utils::{is_low_quality, strip_thinking};
use indexmap::IndexMap;
use once_cell::sync::Lazy;
use serde_json::{json, Map, Value};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

/// `RESEARCH_DATA_DIR = Path("data/deep_research")`.
///
/// DEVIATION (documented above): resolved under `core::constants::DATA_DIR`
/// (honors `ODYSSEUS_DATA_DIR`) rather than the literal source-relative
/// `"data/deep_research"`.
pub static RESEARCH_DATA_DIR: Lazy<String> = Lazy::new(|| os::path::join(&DATA_DIR, "deep_research"));

fn session_json_path(session_id: &str) -> String {
    os::path::join(&RESEARCH_DATA_DIR, &format!("{session_id}.json"))
}

// ---------------------------------------------------------------------------
// DeepResearcher engine seam
// ---------------------------------------------------------------------------

/// The subset of the `DeepResearcher` surface `ResearchHandler` reads.
///
/// Faithful port of the handler's view of `src.deep_research.DeepResearcher`:
/// `research(question, prior_report, prior_findings, prior_urls)`,
/// `get_stats()`, the `findings` list, the `evolving_report` string, and
/// `cancel()`. The concrete engine lives in `crate::src::deep_research`; this
/// trait lets the handler logic (registry, persistence, formatting) be ported
/// and tested independently of the engine's availability.
#[allow(clippy::too_many_arguments)]
#[async_trait::async_trait]
pub trait ResearchEngine: Send + Sync {
    /// `await researcher.research(question, prior_report=..., prior_findings=..., prior_urls=...)`.
    async fn research(
        &self,
        question: &str,
        prior_report: &str,
        prior_findings: &[Value],
        prior_urls: &[String],
    ) -> String;
    /// `researcher.get_stats()` — the metric dict rendered in the report/summary.
    fn get_stats(&self) -> Map<String, Value>;
    /// `researcher.findings` — the per-source findings list (list of dicts).
    fn findings(&self) -> Vec<Value>;
    /// `researcher.evolving_report` — the pre-synthesis report (salvaged on timeout).
    fn evolving_report(&self) -> String;
    /// `researcher.cancel()` — cooperative cancellation request.
    fn cancel(&self);
}

/// Construction parameters for a [`ResearchEngine`], mirroring the
/// `DeepResearcher(...)` keyword arguments `call_research_service` passes.
#[derive(Clone)]
pub struct ResearcherConfig {
    pub llm_endpoint: String,
    pub llm_model: String,
    pub llm_headers: IndexMap<String, String>,
    pub max_rounds: i64,
    pub min_rounds: i64,
    pub max_time: i64,
    pub max_report_tokens: i64,
    /// `extraction_timeout` — per-URL extraction wall-clock (settings-resolved,
    /// bounded [15, 3600]); `0`/unset resolves from
    /// `research_extraction_timeout_seconds` inside `call_research_service`.
    pub extraction_timeout: i64,
    /// `extraction_concurrency` — parallel extraction fan-out (settings-resolved,
    /// bounded [1, 12]); `0`/unset resolves from
    /// `research_extraction_concurrency` inside `call_research_service`.
    pub extraction_concurrency: i64,
    pub search_provider: Option<String>,
    pub category: Option<String>,
}

/// Adapter wrapping the ported `crate::src::deep_research::DeepResearcher` as a
/// [`ResearchEngine`] trait object.
///
/// The Python handler keeps one `researcher` object in the registry entry,
/// mutating it in place during `research()` and reading `.findings` /
/// `.evolving_report` / `.get_stats()` (and calling `.cancel()`) from elsewhere
/// — interleaved cooperatively by single-threaded asyncio. The Rust
/// `DeepResearcher::research` takes `&mut self`, so the engine lives behind a
/// `Mutex`; `research()` holds it for the run's duration, and the registry's
/// reads (salvage on timeout, post-completion source extraction) acquire it only
/// after the run's future has been dropped/finished. `cancel()` flips the
/// engine's `Arc<AtomicBool>` handle directly (lock-free), so cancellation works
/// while a `research()` call still holds the `Mutex`.
struct DeepResearcherAdapter {
    // `tokio::sync::Mutex` (not `std::sync::Mutex`) so the guard is `Send` across
    // the `research(...).await` — the `ResearchEngine` trait's `async_trait`
    // boxing requires `Send` futures.
    inner: tokio::sync::Mutex<crate::src::deep_research::DeepResearcher>,
    cancel: Arc<std::sync::atomic::AtomicBool>,
}

#[async_trait::async_trait]
impl ResearchEngine for DeepResearcherAdapter {
    async fn research(
        &self,
        question: &str,
        prior_report: &str,
        prior_findings: &[Value],
        prior_urls: &[String],
    ) -> String {
        let prior_findings = if prior_findings.is_empty() {
            None
        } else {
            Some(prior_findings.to_vec())
        };
        let prior_urls = if prior_urls.is_empty() {
            None
        } else {
            Some(prior_urls.iter().cloned().collect::<std::collections::HashSet<String>>())
        };
        let mut guard = self.inner.lock().await;
        guard.research(question, prior_report, prior_findings, prior_urls).await
    }
    fn get_stats(&self) -> Map<String, Value> {
        // DeepResearcher::get_stats returns an IndexMap; serde_json's Map (with
        // preserve_order) is an IndexMap, so re-key in iteration order. These read
        // accessors are only invoked after the `research(...)` future has finished
        // or been dropped (success path / timeout-salvage path), so the
        // `tokio::sync::Mutex` is uncontended; `try_lock` keeps the accessor
        // synchronous (the trait defines it as `fn`). If somehow still held we
        // return the empty/default shape — "no data yet".
        match self.inner.try_lock() {
            Ok(guard) => {
                let mut m = Map::new();
                for (k, v) in guard.get_stats() {
                    m.insert(k, v);
                }
                m
            }
            Err(_) => Map::new(),
        }
    }
    fn findings(&self) -> Vec<Value> {
        match self.inner.try_lock() {
            Ok(guard) => guard.findings.clone(),
            Err(_) => Vec::new(),
        }
    }
    fn evolving_report(&self) -> String {
        match self.inner.try_lock() {
            Ok(guard) => guard.evolving_report.clone(),
            Err(_) => String::new(),
        }
    }
    fn cancel(&self) {
        self.cancel.store(true, std::sync::atomic::Ordering::SeqCst);
    }
}

/// Engine factory: construct the real `DeepResearcher` from the handler config,
/// wiring the progress callback so the engine's `_emit` events land in the
/// registry entry's `progress` field (Python's `on_progress`).
fn build_researcher(
    cfg: &ResearcherConfig,
    progress: Arc<dyn Fn(Value) + Send + Sync>,
) -> Arc<dyn ResearchEngine> {
    let mut dr = crate::src::deep_research::DeepResearcher::new(
        cfg.llm_endpoint.clone(),
        cfg.llm_model.clone(),
    )
    .with_headers(if cfg.llm_headers.is_empty() {
        None
    } else {
        Some(cfg.llm_headers.clone())
    })
    .with_search_provider(cfg.search_provider.clone())
    .with_category(cfg.category.clone())
    .with_progress(progress);
    // Remaining keyword args set directly (no builder setter needed).
    dr.max_rounds = cfg.max_rounds;
    dr.min_rounds = cfg.min_rounds;
    dr.max_time = cfg.max_time;
    dr.max_report_tokens = cfg.max_report_tokens;
    // CONVERGENCE GAP: Python also passes `extraction_timeout=` /
    // `extraction_concurrency=` to `DeepResearcher(...)`. The settings-resolved,
    // clamped values are computed in `call_research_service` and carried on
    // `cfg.extraction_timeout` / `cfg.extraction_concurrency`, but the ported
    // `crate::src::deep_research::DeepResearcher` does not yet expose those
    // fields/setters (see deep_research.py L198/217-218). Wiring them is a
    // one-line change in deep_research.rs (`dr.extraction_timeout = ...`) that
    // belongs to that file's port; intentionally NOT touched here (single-file
    // edit rule). Until then the engine uses its own internal defaults.
    let _ = (cfg.extraction_timeout, cfg.extraction_concurrency);
    let cancel = dr.cancel_handle();
    Arc::new(DeepResearcherAdapter {
        inner: tokio::sync::Mutex::new(dr),
        cancel,
    })
}

// ---------------------------------------------------------------------------
// Task registry entry
// ---------------------------------------------------------------------------

/// Registry entry — the Python `entry` dict, as a typed struct.
///
/// The dynamic `dict` keys map to fields; `progress` stays a `Value` because the
/// engine emits arbitrary `_emit`-shaped event objects.
#[derive(Default)]
struct TaskEntry {
    researcher: Option<Arc<dyn ResearchEngine>>,
    handle: Option<tokio::task::JoinHandle<()>>,
    query: String,
    status: String,
    progress: Value,
    result: Option<String>,
    started_at: f64,
    category: Option<String>,
    owner: String,
    // Cached on completion (Python sets entry["sources"] / ["raw_report"] / ["stats"]).
    sources: Option<Vec<Value>>,
    raw_report: String,
    stats: Option<Map<String, Value>>,
}

/// Callback fired once on completion: `on_complete(session_id, result, sources, findings)`.
pub type OnComplete = Arc<dyn Fn(String, String, Vec<Value>, Vec<Value>) + Send + Sync>;

/// `start_research(...)` keyword arguments, mirroring the Python signature.
pub struct StartResearchArgs {
    pub session_id: String,
    pub query: String,
    pub llm_endpoint: String,
    pub llm_model: String,
    pub max_time: i64,
    /// `hard_timeout: int = None` — wall-clock cap. `None` (the default) resolves
    /// from the `research_run_timeout_seconds` setting (default 1800; `0` =
    /// unlimited; any other value clamped to [60, 86400]). A caller that pins a
    /// value bypasses the settings lookup.
    pub hard_timeout: Option<i64>,
    pub llm_headers: IndexMap<String, String>,
    pub on_complete: Option<OnComplete>,
    pub prior_report: String,
    pub prior_findings: Vec<Value>,
    pub prior_urls: Vec<String>,
    pub max_rounds: i64,
    pub search_provider: Option<String>,
    pub category: Option<String>,
    /// `extraction_timeout: int = None` — per-URL extraction timeout override;
    /// `None` resolves from `research_extraction_timeout_seconds`.
    pub extraction_timeout: Option<i64>,
    /// `extraction_concurrency: int = None` — extraction fan-out override; `None`
    /// resolves from `research_extraction_concurrency`.
    pub extraction_concurrency: Option<i64>,
    pub owner: String,
}

impl Default for StartResearchArgs {
    fn default() -> Self {
        StartResearchArgs {
            session_id: String::new(),
            query: String::new(),
            llm_endpoint: String::new(),
            llm_model: String::new(),
            // Python defaults: max_time=300, hard_timeout=None, max_rounds=20.
            // hard_timeout=None => resolve from settings in start_research.
            max_time: 300,
            hard_timeout: None,
            llm_headers: IndexMap::new(),
            on_complete: None,
            prior_report: String::new(),
            prior_findings: Vec::new(),
            prior_urls: Vec::new(),
            max_rounds: 20,
            search_provider: None,
            category: None,
            // extraction_timeout=None / extraction_concurrency=None => resolve
            // from settings inside call_research_service.
            extraction_timeout: None,
            extraction_concurrency: None,
            owner: String::new(),
        }
    }
}

// ---------------------------------------------------------------------------
// ResearchHandler
// ---------------------------------------------------------------------------

/// Handles research service operations with iterative deep research.
pub struct ResearchHandler {
    // `self._legacy_engine = None` — permanently None (research_engine.py never
    // existed in this tree; the Python ImportError branch is the live one).
    // `self._active_tasks: Dict[str, dict]`.
    active_tasks: Arc<Mutex<HashMap<String, Arc<Mutex<TaskEntry>>>>>,
}

impl Default for ResearchHandler {
    fn default() -> Self {
        Self::new()
    }
}

impl ResearchHandler {
    /// `ResearchHandler.__init__` — initialize the (absent) legacy engine and
    /// ensure the research data dir exists.
    pub fn new() -> Self {
        // `self._initialize_legacy_engine()` — Python logs
        // "Legacy research_engine.py not found — DeepResearcher only" and sets None.
        logger::info("Legacy research_engine.py not found — DeepResearcher only");
        // `RESEARCH_DATA_DIR.mkdir(parents=True, exist_ok=True)`.
        let _ = os::makedirs(&RESEARCH_DATA_DIR, true);
        ResearchHandler {
            active_tasks: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    // ------------------------------------------------------------------
    // Query synthesis & planning
    // ------------------------------------------------------------------

    /// Synthesize the conversation into a single focused research query.
    ///
    /// Reads the session history and latest message to produce a clear, specific
    /// research question. Falls back to the latest message if synthesis fails.
    pub async fn synthesize_query(
        &self,
        sess: &crate::core::models::Session,
        latest_message: &str,
        llm_endpoint: &str,
        llm_model: &str,
        llm_headers: IndexMap<String, String>,
    ) -> String {
        // `history = getattr(sess, 'history', [])`.
        let history = &sess.history;

        // A bare affirmation ("yes", "ok", "go ahead") is the user accepting the
        // clarifying-question round, NOT a research topic — researching the word
        // "yes" is the classic failure here. When synthesis can't run or fails,
        // fall back to the earliest substantive user message (the original ask)
        // rather than the literal follow-up.
        //
        // Match on an explicit affirmation/continuation phrase only (plus the
        // empty/punctuation-only case). We deliberately do NOT use a length
        // heuristic: a short answer like "UK", "C++", or "Rust" is a real topic
        // in a clarification flow and must be left untouched.
        let fallback = || -> String {
            // `_fallback()`: prefer the latest message unless it's a pure
            // affirmation / empty; otherwise the earliest substantive user msg.
            let normalized = normalize_affirmation(latest_message);
            if !normalized.is_empty() && !is_affirmation(&normalized) {
                return latest_message.to_string(); // short or long, it's a real topic
            }
            // Affirmation, or empty/punctuation-only: use the original ask.
            for m in history.iter() {
                let c = m.content.trim();
                if m.role == "user" && !c.is_empty() && !is_affirmation(&normalize_affirmation(c)) {
                    return c.to_string();
                }
            }
            latest_message.to_string()
        };

        // `if len(history) <= 1: return _fallback()`.
        if history.len() <= 1 {
            return fallback(); // No conversation to synthesize
        }

        // `recent = history[-6:]`.
        let recent = if history.len() > 6 {
            &history[history.len() - 6..]
        } else {
            &history[..]
        };
        // `convo = "\n".join(... for m in recent if m.content)`.
        let mut convo = recent
            .iter()
            .filter(|m| !m.content.is_empty())
            .map(|m| {
                let role = if m.role == "user" { "User" } else { "Assistant" };
                // `m.content[:500]`.
                format!("{}: {}", role, char_slice(&m.content, 500))
            })
            .collect::<Vec<_>>()
            .join("\n");
        // `convo += f"\nUser: {latest_message}"`.
        convo.push_str(&format!("\nUser: {latest_message}"));

        let prompt = format!(
            "Read this conversation and write a single, specific research query that captures \
what the user wants to know. Include all relevant context, constraints, and preferences \
they mentioned. Output ONLY the research query — nothing else.\n\nConversation:\n{convo}"
        );
        // try: response = await llm_call_async(...); query = strip_thinking(response).strip().strip('"\'')
        match llm_call_async(
            llm_endpoint,
            llm_model,
            vec![json!({"role": "user", "content": prompt})],
            0.1,
            200,
            llm_headers,
            15,
        )
        .await
        {
            Ok(response) => {
                let stripped = strip_thinking(Some(&response)).unwrap_or_default();
                // `.strip().strip('"\'')` — strip whitespace, then leading/trailing `"`/`'`.
                let query = stripped.trim().trim_matches(|c| c == '"' || c == '\'');
                // `if query and len(query) > 5: return query`.
                if !query.is_empty() && char_len(query) > 5 {
                    return query.to_string();
                }
            }
            Err(e) => {
                logger::warning(&format!("Query synthesis failed: {e}"));
            }
        }

        fallback()
    }

    /// Generate a research plan for user review before starting research.
    pub async fn generate_plan(
        &self,
        query: &str,
        llm_endpoint: &str,
        llm_model: &str,
        llm_headers: IndexMap<String, String>,
    ) -> Option<Value> {
        // `prompt = current_date_context() + RESEARCH_PLAN_PROMPT.format(question=query)`.
        let prompt = format!("{}{}", current_date_context(), research_plan_prompt(query));
        let response = match llm_call_async(
            llm_endpoint,
            llm_model,
            vec![json!({"role": "user", "content": prompt})],
            0.3,
            1024,
            llm_headers,
            30,
        )
        .await
        {
            Ok(r) => r,
            Err(e) => {
                logger::warning(&format!("Research plan generation failed: {e}"));
                return None;
            }
        };
        let response = strip_thinking(Some(&response)).unwrap_or_default();

        // Try to parse structured plan: strip code fences, then extract the first {...} block.
        let mut parsed: Option<Value> = None;
        let mut clean = response.trim().to_string();
        if clean.starts_with("```") {
            // re.sub(r'^```(?:json)?\s*', '', clean) ; re.sub(r'\s*```$', '', clean)
            clean = FENCE_OPEN_RE.replace(&clean, "").into_owned();
            clean = FENCE_CLOSE_RE.replace(&clean, "").into_owned();
        }
        if let Some(m) = JSON_OBJ_RE.find(&clean) {
            if let Ok(v) = serde_json::from_str::<Value>(m.as_str()) {
                parsed = Some(v);
            }
        }

        // Build the plan dict. Python uses parsed.get(...) only when parsed is truthy.
        let get_or = |key: &str, default: Value| -> Value {
            match &parsed {
                Some(p) => p.get(key).cloned().unwrap_or(default),
                None => default,
            }
        };
        Some(json!({
            "sub_questions": get_or("sub_questions", json!([])),
            "key_topics": get_or("key_topics", json!([])),
            "success_criteria": get_or("success_criteria", json!("")),
            "raw": response,
        }))
    }

    // ------------------------------------------------------------------
    // Task registry — background research with persistence
    // ------------------------------------------------------------------

    /// Start research as a background task. Returns task info dict.
    ///
    /// `max_rounds` is the safety cap; the AI's `_should_stop` decision (after
    /// `min_rounds`) terminates the loop earlier in normal operation.
    pub fn start_research(self: &Arc<Self>, args: StartResearchArgs) -> Value {
        let session_id = args.session_id.clone();

        // Resolve the hard wall-clock timeout from settings when the caller
        // didn't pin one. Local / edge models routinely need more than the old
        // 600s default to finish a deep-research synthesis. A setting of 0
        // disables the cap entirely (unlimited run); any other value is bounded
        // to [60, 86400] so a misconfigured settings.json can't explode into a
        // multi-day hang. Only the SETTINGS-resolved path treats `<= 0` as
        // unlimited (`raw_timeout <= 0` in Python); a caller that explicitly
        // pins a value is used literally (matching Python `hard_timeout is None`
        // gating the whole resolution block).
        //
        // `None` (the resolved inner value) means "no wall-clock cap";
        // `Some(secs)` is the bounded cap.
        let hard_timeout: Option<i64> = match args.hard_timeout {
            None => {
                // raw_timeout = int(get_setting("research_run_timeout_seconds", 1800))
                let raw = crate::src::settings::get_setting(
                    "research_run_timeout_seconds",
                    json!(1800),
                );
                let raw_timeout = parse_int_or(&raw, 1800);
                if raw_timeout <= 0 {
                    None // 0 = no wall-clock cap
                } else {
                    Some(bounded_int(&json!(raw_timeout), 1800, 60, 86400))
                }
            }
            // Caller pinned a value: used as-is (Python skips the resolution
            // block entirely when hard_timeout is not None).
            Some(v) => Some(v),
        };

        // Cancel any existing running research for this session.
        let existing_running = {
            let tasks = self.active_tasks.lock().unwrap();
            tasks
                .get(&session_id)
                .map(|e| e.lock().unwrap().status == "running")
                .unwrap_or(false)
        };
        if existing_running {
            self.cancel_research(&session_id);
        }

        // entry = {...}; self._active_tasks[session_id] = entry
        let entry = Arc::new(Mutex::new(TaskEntry {
            researcher: None,
            handle: None,
            query: args.query.clone(),
            status: "running".to_string(),
            progress: json!({}),
            result: None,
            started_at: pytime::time(),
            category: args.category.clone(),
            owner: args.owner.clone(),
            sources: None,
            raw_report: String::new(),
            stats: None,
        }));
        self.active_tasks
            .lock()
            .unwrap()
            .insert(session_id.clone(), entry.clone());

        let query = args.query.clone();
        // Separate clone moved into the spawned task; `query` is still needed for
        // the synchronous `{"status": "running"}` return value below.
        let query_for_task = query.clone();
        let handler = Arc::clone(self);
        let entry_for_task = entry.clone();
        let cfg = ResearcherConfig {
            llm_endpoint: args.llm_endpoint.clone(),
            llm_model: args.llm_model.clone(),
            llm_headers: args.llm_headers.clone(),
            max_rounds: args.max_rounds,
            // min_rounds=min(3, max_rounds).
            min_rounds: std::cmp::min(3, args.max_rounds),
            max_time: args.max_time,
            // Filled from settings inside call_research_service.
            max_report_tokens: 0,
            // Caller override (or 0 = "unset, resolve from settings inside
            // call_research_service"). Python passes extraction_timeout /
            // extraction_concurrency (each `None` by default) straight through
            // to call_research_service, which falls back to the settings value.
            extraction_timeout: args.extraction_timeout.unwrap_or(0),
            extraction_concurrency: args.extraction_concurrency.unwrap_or(0),
            search_provider: args.search_provider.clone(),
            category: args.category.clone(),
        };
        let on_complete = args.on_complete.clone();
        // `hard_timeout` is the SETTINGS-RESOLVED Option<i64> bound at the top of
        // start_research (None = unlimited); do NOT re-read args.hard_timeout
        // here (that is the unresolved sentinel).
        let max_time = args.max_time;
        let prior_report = args.prior_report.clone();
        let prior_findings = args.prior_findings.clone();
        let prior_urls = args.prior_urls.clone();
        let sid = session_id.clone();

        // async def _run(): hard wall-clock timeout salvaging partial results.
        let task = tokio::spawn(async move {
            // `_completed` guard for the single on_complete fire.
            let mut completed = false;
            let mut guarded_complete =
                |result: String, sources: Vec<Value>, findings: Vec<Value>| {
                    if completed {
                        return;
                    }
                    completed = true;
                    if let Some(cb) = &on_complete {
                        cb(sid.clone(), result, sources, findings);
                    }
                };

            let fut = handler.call_research_service(
                &query_for_task,
                &cfg,
                max_time,
                Some(&entry_for_task),
                &prior_report,
                &prior_findings,
                &prior_urls,
            );
            // `hard_timeout` text used in the salvage / error messages. Python
            // interpolates `{hard_timeout}s`; with an unlimited cap (None) the
            // timeout branch can never fire, so the label only matters for the
            // bounded case.
            let hard_timeout_label = hard_timeout.map(|s| s.to_string()).unwrap_or_default();
            // `asyncio.wait_for(coro, timeout=hard_timeout)` — `timeout=None`
            // (our `None`) means no wall-clock cap, so just await the future.
            let outcome: Result<String, ()> = match hard_timeout {
                Some(secs) => {
                    let dur = std::time::Duration::from_secs(secs.max(0) as u64);
                    tokio::time::timeout(dur, fut).await.map_err(|_| ())
                }
                None => Ok(fut.await),
            };
            match outcome {
                Ok(result) => {
                    {
                        let mut e = entry_for_task.lock().unwrap();
                        e.result = Some(result.clone());
                        e.status = "done".to_string();
                    }
                    handler.save_result(&sid, &entry_for_task);
                    // Persist to DB via callback.
                    let (sources, findings) = {
                        let e = entry_for_task.lock().unwrap();
                        let sources = e.sources.clone().unwrap_or_default();
                        let findings = match &e.researcher {
                            Some(r) => {
                                let f = r.findings();
                                if f.is_empty() {
                                    Vec::new()
                                } else {
                                    extract_raw_findings(&f)
                                }
                            }
                            None => Vec::new(),
                        };
                        (sources, findings)
                    };
                    guarded_complete(result, sources, findings);
                }
                Err(()) => {
                    // asyncio.TimeoutError branch — salvage evolving_report.
                    // Only reachable on the bounded (Some) path.
                    logger::error(&format!(
                        "Research hard timeout ({hard_timeout_label}s) for session {sid}"
                    ));
                    {
                        let mut e = entry_for_task.lock().unwrap();
                        e.status = "error".to_string();
                    }
                    let salvage = {
                        let e = entry_for_task.lock().unwrap();
                        e.researcher.as_ref().and_then(|r| {
                            let report = r.evolving_report();
                            if report.is_empty() {
                                None
                            } else {
                                let stats = r.get_stats();
                                let findings = r.findings();
                                Some((report, stats, findings))
                            }
                        })
                    };
                    if let Some((report, stats, findings)) = salvage {
                        let formatted = format_research_report(
                            &query_for_task,
                            &report,
                            &stats,
                            // hard_timeout is Some on this branch (timeout fired).
                            hard_timeout.unwrap_or(0) as f64,
                        );
                        {
                            let mut e = entry_for_task.lock().unwrap();
                            e.result = Some(formatted.clone());
                            e.status = "done".to_string();
                        }
                        handler.save_result(&sid, &entry_for_task);
                        let sources = if findings.is_empty() {
                            Vec::new()
                        } else {
                            extract_sources(&findings)
                        };
                        let raw = if findings.is_empty() {
                            Vec::new()
                        } else {
                            extract_raw_findings(&findings)
                        };
                        guarded_complete(formatted, sources, raw);
                    } else {
                        let mut e = entry_for_task.lock().unwrap();
                        e.result = Some(format!(
                            "Research timed out after {hard_timeout_label}s. The model may be too slow for deep research."
                        ));
                    }
                    let mut e = entry_for_task.lock().unwrap();
                    e.progress = json!({
                        "phase": "error",
                        "message": format!("Research timed out after {hard_timeout_label}s"),
                    });
                }
            }
        });

        entry.lock().unwrap().handle = Some(task);
        json!({"session_id": session_id, "status": "running", "query": query})
    }

    /// Get current research status for a session.
    pub fn get_status(&self, session_id: &str) -> Option<Value> {
        let avg = self.get_avg_duration();
        {
            let tasks = self.active_tasks.lock().unwrap();
            if let Some(entry) = tasks.get(session_id) {
                let e = entry.lock().unwrap();
                let mut result = json!({
                    "status": e.status,
                    "progress": e.progress,
                    "query": e.query,
                    "started_at": e.started_at,
                });
                if let Some(avg) = avg {
                    // round(avg, 1)
                    result["avg_duration"] = json!(round_half_even(avg, 1));
                }
                return Some(result);
            }
        }
        // Check disk for completed research (skip consumed results).
        let path = session_json_path(session_id);
        if os::path::exists(&path) {
            if let Some(data) = read_json_object(&path) {
                if truthy(data.get("consumed")) {
                    return None;
                }
                return Some(json!({
                    "status": data.get("status").cloned().unwrap_or(json!("done")),
                    "progress": {},
                    "query": data.get("query").cloned().unwrap_or(json!("")),
                    "started_at": data.get("started_at").cloned().unwrap_or(json!(0)),
                }));
            }
        }
        None
    }

    /// `routes/research_routes.py` accessor: the absolute on-disk JSON path for a
    /// session (`RESEARCH_DATA_DIR / f"{session_id}.json"`).
    ///
    /// Python's `research_routes` builds `Path("data/deep_research") /
    /// f"{session_id}.json"` directly; the ported handler centralizes the path
    /// scheme (which honors the documented `DATA_DIR` deviation), so the routes
    /// read/write the SAME files the handler does by going through this accessor.
    pub fn session_json_path(&self, session_id: &str) -> String {
        session_json_path(session_id)
    }

    /// `routes/research_routes.py::_owns_in_memory` accessor: the `owner` of an
    /// in-flight (in-memory) research task, or `None` when the task is not in the
    /// registry (the route then falls back to the on-disk JSON).
    ///
    /// Mirrors `entry = research_handler._active_tasks.get(session_id)` followed by
    /// `entry.get("owner", "")` — present (`Some`) only when there is a registry
    /// entry, so the route can distinguish "no in-memory task" (`None`, check disk)
    /// from "in-memory task with owner X".
    pub fn active_owner(&self, session_id: &str) -> Option<String> {
        let tasks = self.active_tasks.lock().unwrap();
        tasks
            .get(session_id)
            .map(|entry| entry.lock().unwrap().owner.clone())
    }

    /// `routes/research_routes.py::research_active` accessor: the running tasks
    /// belonging to `owner`, each as `{session_id, query, status, progress,
    /// started_at}` (status is always `"running"`).
    ///
    /// Mirrors the Python loop:
    /// ```python
    /// for sid, entry in research_handler._active_tasks.items():
    ///     if entry.get("owner", "") != user: continue
    ///     if entry.get("status") == "running":
    ///         active.append({"session_id": sid, "query": entry.get("query",""),
    ///                        "status": "running", "progress": entry.get("progress",{}),
    ///                        "started_at": entry.get("started_at", 0)})
    /// ```
    /// Iteration order follows the underlying `HashMap`, matching Python `dict`
    /// iteration being insertion-order-independent here (the route returns a list
    /// the frontend treats as a set).
    pub fn active_running(&self, owner: &str) -> Vec<Value> {
        let tasks = self.active_tasks.lock().unwrap();
        let mut active: Vec<Value> = Vec::new();
        for (sid, entry) in tasks.iter() {
            let e = entry.lock().unwrap();
            // SECURITY: only show this user's running tasks.
            if e.owner != owner {
                continue;
            }
            if e.status == "running" {
                active.push(json!({
                    "session_id": sid,
                    "query": e.query,
                    "status": "running",
                    "progress": e.progress,
                    "started_at": e.started_at,
                }));
            }
        }
        active
    }

    /// `routes/research_routes.py::research_stream` accessor: the `result` field of
    /// the in-memory task entry, or `None` when there is no registry entry / the
    /// entry has no result yet.
    ///
    /// Mirrors `task = research_handler._active_tasks.get(session_id, {})` then
    /// `task.get("result")` (used only on the `status == "error"` SSE branch).
    pub fn active_result(&self, session_id: &str) -> Option<String> {
        let tasks = self.active_tasks.lock().unwrap();
        tasks
            .get(session_id)
            .and_then(|entry| entry.lock().unwrap().result.clone())
    }

    /// Cancel running research for a session.
    pub fn cancel_research(&self, session_id: &str) -> bool {
        let tasks = self.active_tasks.lock().unwrap();
        let entry = match tasks.get(session_id) {
            Some(e) => e,
            None => return false,
        };
        let mut e = entry.lock().unwrap();
        if e.status != "running" {
            return false;
        }
        if let Some(r) = &e.researcher {
            r.cancel();
        }
        // task.cancel() — abort the tokio task if not finished.
        if let Some(h) = &e.handle {
            if !h.is_finished() {
                h.abort();
            }
        }
        e.status = "cancelled".to_string();
        true
    }

    /// Get the completed research result.
    pub fn get_result(&self, session_id: &str) -> Option<String> {
        {
            let tasks = self.active_tasks.lock().unwrap();
            if let Some(entry) = tasks.get(session_id) {
                let e = entry.lock().unwrap();
                if matches!(e.status.as_str(), "done" | "error" | "cancelled") {
                    return e.result.clone();
                }
            }
        }
        let path = session_json_path(session_id);
        if os::path::exists(&path) {
            if let Some(data) = read_json_object(&path) {
                if truthy(data.get("consumed")) {
                    return None;
                }
                return data.get("result").and_then(|v| v.as_str()).map(String::from);
            }
        }
        None
    }

    /// Get deduplicated source list from research findings.
    pub fn get_sources(&self, session_id: &str) -> Option<Vec<Value>> {
        {
            let tasks = self.active_tasks.lock().unwrap();
            if let Some(entry) = tasks.get(session_id) {
                let e = entry.lock().unwrap();
                // `if entry.get("sources"):` — truthy (present and non-empty).
                if let Some(s) = &e.sources {
                    if !s.is_empty() {
                        return Some(s.clone());
                    }
                }
                if let Some(r) = &e.researcher {
                    let f = r.findings();
                    if !f.is_empty() {
                        return Some(extract_sources(&f));
                    }
                }
            }
        }
        let path = session_json_path(session_id);
        if os::path::exists(&path) {
            if let Some(data) = read_json_object(&path) {
                return data
                    .get("sources")
                    .and_then(|v| v.as_array())
                    .cloned();
            }
        }
        None
    }

    /// Get raw per-source findings for display.
    pub fn get_raw_findings(&self, session_id: &str) -> Option<Vec<Value>> {
        {
            let tasks = self.active_tasks.lock().unwrap();
            if let Some(entry) = tasks.get(session_id) {
                let e = entry.lock().unwrap();
                if let Some(r) = &e.researcher {
                    let f = r.findings();
                    if !f.is_empty() {
                        return Some(extract_raw_findings(&f));
                    }
                }
            }
        }
        let path = session_json_path(session_id);
        if os::path::exists(&path) {
            match read_json_object(&path) {
                Some(data) => {
                    return data
                        .get("raw_findings")
                        .and_then(|v| v.as_array())
                        .cloned();
                }
                None => {
                    logger::warning(&format!("Failed to read raw findings for {session_id}"));
                }
            }
        }
        None
    }

    /// Compute average research duration from completed results on disk.
    pub fn get_avg_duration(&self) -> Option<f64> {
        let mut durations: Vec<f64> = Vec::new();
        // for p in RESEARCH_DATA_DIR.glob("*.json"):
        if let Ok(rd) = std::fs::read_dir(&*RESEARCH_DATA_DIR) {
            for ent in rd.flatten() {
                let path = ent.path();
                if path.extension().and_then(|e| e.to_str()) != Some("json") {
                    continue;
                }
                let p = match path.to_str() {
                    Some(p) => p,
                    None => continue,
                };
                if let Some(data) = read_json_object(p) {
                    if data.get("status").and_then(|v| v.as_str()) == Some("done") {
                        let started = data.get("started_at").and_then(|v| v.as_f64()).unwrap_or(0.0);
                        let completed = data.get("completed_at").and_then(|v| v.as_f64()).unwrap_or(0.0);
                        // `if started and completed and completed > started:`.
                        if started != 0.0 && completed != 0.0 && completed > started {
                            durations.push(completed - started);
                        }
                    }
                }
            }
        }
        if !durations.is_empty() {
            return Some(durations.iter().sum::<f64>() / durations.len() as f64);
        }
        None
    }

    /// Mark result as consumed so it won't be re-rendered on refresh.
    ///
    /// Keeps the JSON on disk so visual reports can be generated later.
    pub fn clear_result(&self, session_id: &str) {
        // `self._active_tasks.pop(session_id, None)`.
        self.active_tasks.lock().unwrap().remove(session_id);
        let path = session_json_path(session_id);
        if os::path::exists(&path) {
            if let Some(mut data) = read_json_object(&path) {
                data.insert("consumed".to_string(), json!(true));
                // path.write_text(json.dumps(data)) — compact (no indent).
                let _ = write_json_compact(&path, &data);
            }
        }
    }

    /// Persist completed research result to disk.
    fn save_result(&self, session_id: &str, entry: &Arc<Mutex<TaskEntry>>) {
        // Extract and cache sources + raw findings.
        let mut sources: Vec<Value> = Vec::new();
        let mut raw_findings: Vec<Value> = Vec::new();
        {
            let e = entry.lock().unwrap();
            if let Some(r) = &e.researcher {
                let f = r.findings();
                if !f.is_empty() {
                    sources = extract_sources(&f);
                    raw_findings = extract_raw_findings(&f);
                }
            }
        }
        let owner;
        let data = {
            let mut e = entry.lock().unwrap();
            e.sources = Some(sources.clone());
            owner = e.owner.clone();
            let mut m = Map::new();
            m.insert("query".to_string(), json!(e.query));
            m.insert("status".to_string(), json!(e.status));
            m.insert(
                "result".to_string(),
                e.result.clone().map(Value::String).unwrap_or(Value::Null),
            );
            m.insert("raw_report".to_string(), json!(e.raw_report));
            m.insert("sources".to_string(), Value::Array(sources.clone()));
            m.insert("raw_findings".to_string(), Value::Array(raw_findings));
            m.insert(
                "stats".to_string(),
                e.stats.clone().map(Value::Object).unwrap_or(Value::Null),
            );
            m.insert(
                "category".to_string(),
                e.category.clone().map(Value::String).unwrap_or(Value::Null),
            );
            m.insert("started_at".to_string(), json!(e.started_at));
            m.insert("completed_at".to_string(), json!(pytime::time()));
            m.insert("owner".to_string(), json!(e.owner));
            m
        };
        let path = session_json_path(session_id);
        match write_json_compact(&path, &data) {
            Ok(()) => {
                logger::info(&format!("Research result saved to {path}"));
                // fire_event("research_completed", entry.get("owner") or None) —
                // empty owner is falsy in Python, so it collapses to None.
                let ev_owner = if owner.is_empty() { None } else { Some(owner.as_str()) };
                crate::src::event_bus::fire_event("research_completed", ev_owner);
            }
            Err(e) => {
                logger::error(&format!("Failed to save research result: {e}"));
            }
        }
    }

    /// Generate the visual HTML report for a session (always fresh from JSON).
    pub fn get_report_html(&self, session_id: &str) -> Option<String> {
        let json_path = session_json_path(session_id);
        if !os::path::exists(&json_path) {
            logger::warning(&format!("No JSON found for visual report: {json_path}"));
            return None;
        }
        let data = match read_json_object(&json_path) {
            Some(d) => d,
            None => {
                logger::error("Failed to generate visual report: invalid JSON");
                return None;
            }
        };
        // report_md = data.get("raw_report") or data.get("result", "")
        let raw_report = data.get("raw_report").and_then(|v| v.as_str()).unwrap_or("");
        let report_md = if !raw_report.is_empty() {
            raw_report
        } else {
            data.get("result").and_then(|v| v.as_str()).unwrap_or("")
        };
        let question = data.get("query").and_then(|v| v.as_str()).unwrap_or("");
        let sources = data.get("sources").and_then(|v| v.as_array()).map(|a| a.as_slice());
        let stats = data.get("stats");
        let category = data.get("category").and_then(|v| v.as_str());
        let hidden_images: Vec<String> = data
            .get("hidden_images")
            .and_then(|v| v.as_array())
            .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
            .unwrap_or_default();

        let html = crate::src::visual_report::generate_visual_report(
            question,
            report_md,
            sources,
            stats,
            category,
            Some(session_id),
            Some(&hidden_images),
        );
        logger::info(&format!("Visual report generated for {session_id}"));
        Some(html)
    }

    /// Add `image_url` to the persisted `hidden_images` list for a research.
    pub fn hide_image(&self, session_id: &str, image_url: &str) -> bool {
        let path = session_json_path(session_id);
        if !os::path::exists(&path) {
            return false;
        }
        let mut data = match read_json_object(&path) {
            Some(d) => d,
            None => {
                logger::error("Failed to hide image: invalid JSON");
                return false;
            }
        };
        // hidden = data.get("hidden_images") or []
        let mut hidden: Vec<String> = data
            .get("hidden_images")
            .and_then(|v| v.as_array())
            .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
            .unwrap_or_default();
        if !hidden.iter().any(|h| h == image_url) {
            hidden.push(image_url.to_string());
            data.insert(
                "hidden_images".to_string(),
                Value::Array(hidden.iter().map(|s| json!(s)).collect()),
            );
            if write_json_compact(&path, &data).is_err() {
                logger::error("Failed to hide image");
                return false;
            }
            // f"Hid image {image_url[:80]} for research {session_id}".
            logger::info(&format!(
                "Hid image {} for research {session_id}",
                char_slice(image_url, 80)
            ));
        }
        true
    }

    /// Clear the `hidden_images` list for a research.
    pub fn unhide_all_images(&self, session_id: &str) -> bool {
        let path = session_json_path(session_id);
        if !os::path::exists(&path) {
            return false;
        }
        let mut data = match read_json_object(&path) {
            Some(d) => d,
            None => {
                logger::error("Failed to unhide images: invalid JSON");
                return false;
            }
        };
        data.insert("hidden_images".to_string(), Value::Array(Vec::new()));
        if write_json_compact(&path, &data).is_err() {
            logger::error("Failed to unhide images");
            return false;
        }
        logger::info(&format!("Cleared hidden_images for research {session_id}"));
        true
    }

    /// Quick probe to verify the LLM endpoint/model responds before research.
    pub async fn probe_endpoint(
        &self,
        endpoint: &str,
        model: &str,
        headers: IndexMap<String, String>,
    ) -> PyResult<()> {
        let has_auth = headers.contains_key("Authorization");
        logger::info(&format!("Probing {model} at {endpoint} (has_auth={has_auth})"));
        match llm_call_async(
            endpoint,
            model,
            vec![json!({"role": "user", "content": "hi"})],
            0.0,
            5,
            headers,
            15,
        )
        .await
        {
            Ok(_) => {
                logger::info(&format!("Endpoint probe OK: {model}"));
                Ok(())
            }
            Err(e) => {
                logger::error(&format!("Probe failed for {model}: {e}"));
                // raise RuntimeError(_format_probe_failure(model, e)) from e
                Err(PyError::other(format_probe_failure(model, &e)))
            }
        }
    }

    /// Public one-shot research entry — the Python's simple
    /// `call_research_service(query, llm_endpoint, llm_model, llm_headers=...)`
    /// signature, used by chat `use_research` injection and the `/api/test-research`
    /// diagnostic. Builds a default `ResearcherConfig` (max_time=300, max_rounds=20,
    /// matching the Python defaults) and runs a single, non-task-tracked pass,
    /// returning the formatted report.
    pub async fn run_research_once(
        &self,
        query: &str,
        llm_endpoint: &str,
        llm_model: &str,
        llm_headers: IndexMap<String, String>,
    ) -> String {
        let cfg = ResearcherConfig {
            llm_endpoint: llm_endpoint.to_string(),
            llm_model: llm_model.to_string(),
            llm_headers,
            max_rounds: 20,
            min_rounds: 3,
            max_time: 300,
            max_report_tokens: 0, // filled from settings inside call_research_service
            // 0 = unset => resolved from research_extraction_* settings inside
            // call_research_service.
            extraction_timeout: 0,
            extraction_concurrency: 0,
            search_provider: None,
            category: None,
        };
        self.call_research_service(query, &cfg, 300, None, "", &[], &[])
            .await
    }

    /// Run iterative deep research using the LLM-in-the-loop DeepResearcher.
    ///
    /// Returns a formatted research report with expandable section and summary.
    #[allow(clippy::too_many_arguments)]
    async fn call_research_service(
        &self,
        query: &str,
        cfg: &ResearcherConfig,
        max_time: i64,
        task_entry: Option<&Arc<Mutex<TaskEntry>>>,
        prior_report: &str,
        prior_findings: &[Value],
        prior_urls: &[String],
    ) -> String {
        let is_continuation = !prior_report.is_empty();
        logger::info(&format!(
            "{} IterResearch Deep Research",
            if is_continuation { "Continuing" } else { "Starting" }
        ));
        logger::info(&format!("Query: {query}"));
        logger::info(&format!("LLM: {} / {}", cfg.llm_endpoint, cfg.llm_model));
        logger::info(&format!("Max time: {max_time}s"));
        if is_continuation {
            logger::info(&format!(
                "Prior: {} findings, {} URLs",
                prior_findings.len(),
                prior_urls.len()
            ));
        }

        // Probe the endpoint before committing to a long research run.
        if let Some(entry) = task_entry {
            entry.lock().unwrap().progress = json!({"phase": "probing", "model": cfg.llm_model});
        }
        if let Err(e) = self
            .probe_endpoint(&cfg.llm_endpoint, &cfg.llm_model, cfg.llm_headers.clone())
            .await
        {
            // Python: _probe_endpoint raises -> falls into the outer except below.
            logger::error(&format!("DeepResearcher failed: {e}"));
            return self
                .fallback_research(query, &cfg.llm_endpoint, &cfg.llm_model, max_time, &e.to_string())
                .await;
        }

        // _max_report_tokens = int(get_setting("research_max_tokens", 16384))
        let max_report_tokens =
            crate::src::settings::get_setting("research_max_tokens", json!(16384))
                .as_i64()
                .unwrap_or(16384);
        let mut cfg = cfg.clone();
        cfg.max_report_tokens = max_report_tokens;

        // _extraction_timeout = _bounded_int(
        //     extraction_timeout if extraction_timeout is not None
        //         else get_setting("research_extraction_timeout_seconds", 90),
        //     default=90, minimum=15, maximum=3600)
        //
        // Override (cfg.extraction_timeout != 0) takes precedence; 0 = "unset" =>
        // read the setting. The `_bounded_int` clamp is applied either way.
        let extraction_timeout_src = if cfg.extraction_timeout != 0 {
            json!(cfg.extraction_timeout)
        } else {
            crate::src::settings::get_setting("research_extraction_timeout_seconds", json!(90))
        };
        cfg.extraction_timeout = bounded_int(&extraction_timeout_src, 90, 15, 3600);

        // _extraction_concurrency = _bounded_int(
        //     extraction_concurrency if extraction_concurrency is not None
        //         else get_setting("research_extraction_concurrency", 3),
        //     default=3, minimum=1, maximum=12)
        let extraction_concurrency_src = if cfg.extraction_concurrency != 0 {
            json!(cfg.extraction_concurrency)
        } else {
            crate::src::settings::get_setting("research_extraction_concurrency", json!(3))
        };
        cfg.extraction_concurrency = bounded_int(&extraction_concurrency_src, 3, 1, 12);

        // on_progress(event): entry["progress"] = event. The engine's _emit
        // events land here via the registered progress callback.
        let progress: Arc<dyn Fn(Value) + Send + Sync> = match task_entry {
            Some(entry) => {
                let e = Arc::clone(entry);
                Arc::new(move |event: Value| {
                    e.lock().unwrap().progress = event;
                })
            }
            None => Arc::new(|_event: Value| {}),
        };
        let researcher = build_researcher(&cfg, progress);
        if let Some(entry) = task_entry {
            entry.lock().unwrap().researcher = Some(Arc::clone(&researcher));
        }

        let start_time = pytime::time();
        let report = researcher
            .research(query, prior_report, prior_findings, prior_urls)
            .await;
        let elapsed = pytime::time() - start_time;

        let stats = researcher.get_stats();
        logger::info("IterResearch completed successfully");
        for (key, value) in &stats {
            logger::info(&format!("  {key}: {value}"));
        }

        // Store raw report and stats for visual report generation.
        if let Some(entry) = task_entry {
            let mut e = entry.lock().unwrap();
            e.raw_report = strip_thinking(Some(&report)).unwrap_or_default();
            e.stats = Some(stats.clone());
        }

        format_research_report(query, &report, &stats, elapsed)
    }

    /// Fall back to legacy engine, then to basic web search.
    ///
    /// The legacy `ResearchOrchestrator` is permanently absent (see module docs),
    /// so this always falls straight through to `_handle_research_failure`.
    async fn fallback_research(
        &self,
        query: &str,
        _llm_endpoint: &str,
        _llm_model: &str,
        _max_time: i64,
        primary_error: &str,
    ) -> String {
        // `if self._legacy_engine:` — permanently None; skip.
        self.handle_research_failure(query, primary_error).await
    }

    /// Handle research failure with fallback to basic search.
    ///
    /// Mirrors Python `_handle_research_failure`: run
    /// `crate::src::search::comprehensive_web_search(query)` (all defaults,
    /// `return_sources=False`) inside `spawn_blocking` (the function is
    /// sync-blocking `reqwest`). On success, render the
    /// "Research Failed - Basic Search Fallback" report interpolating
    /// `query` + `error` + the basic-search results; if the fallback search
    /// itself panics, render the "Complete Research Failure" shape with the real
    /// fallback error string.
    async fn handle_research_failure(&self, query: &str, error: &str) -> String {
        use crate::src::search::comprehensive_web_search;
        logger::info("Attempting fallback to basic web search...");

        // search_result = comprehensive_web_search(query) — Python defaults.
        let q = query.to_string();
        let join = tokio::task::spawn_blocking(move || {
            comprehensive_web_search(&q, 3, 4, None, None, None, None, None, 0, false)
        })
        .await;

        match join {
            Ok(result) => {
                let search_result = result.into_report();
                format!(
                    "## Research Failed - Basic Search Fallback\n\n\
**Query:** {query}\n\n\
**Error:** {error}\n\n\
**Note:** The deep research engine encountered an error. Here are basic search results instead:\n\n\
---\n\n\
### Basic Web Search Results\n\n\
{search_result}\n\n\
---\n\n\
**To fix deep research:**\n\
1. Check that your LLM endpoint and search provider are properly configured\n\
2. Verify network connectivity\n\
3. Review application logs for detailed error information\n\n\
Try the web search toggle for simpler queries, or fix the research engine for comprehensive analysis.\n"
                )
            }
            Err(e2) => {
                logger::error(&format!("Fallback search also failed: {e2}"));
                format!(
                    "## Complete Research Failure\n\n\
**Primary Error:** {error}\n\
**Fallback Error:** {e2}\n\n\
**Please check:**\n\
1. Search provider configuration in Settings -> Search Settings\n\
2. Network connectivity to search APIs\n\
3. Application logs for detailed error information\n\
4. That SearXNG is running (if using SearXNG)\n\n\
**Debug Info:**\n\
- Search config endpoint: `/api/search/config`\n\
- Test basic search toggle with a simple query first\n"
                )
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Static helpers (Python @staticmethod / module-level functions)
// ---------------------------------------------------------------------------

/// `_extract_sources(findings)` — deduplicated `[{url, title}]`, filtering
/// low-quality summaries.
pub fn extract_sources(findings: &[Value]) -> Vec<Value> {
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut sources: Vec<Value> = Vec::new();
    for f in findings {
        let url = f.get("url").and_then(|v| v.as_str()).unwrap_or("");
        // title = f.get("title", "") or url
        let title_raw = f.get("title").and_then(|v| v.as_str()).unwrap_or("");
        let title = if title_raw.is_empty() { url } else { title_raw };
        // summary = f.get("summary", "") or f.get("evidence", "")
        let summary_raw = f.get("summary").and_then(|v| v.as_str()).unwrap_or("");
        let summary = if summary_raw.is_empty() {
            f.get("evidence").and_then(|v| v.as_str()).unwrap_or("")
        } else {
            summary_raw
        };
        if !url.is_empty() && !seen.contains(url) && !is_low_quality(summary) {
            seen.insert(url.to_string());
            let mut entry = Map::new();
            entry.insert("url".to_string(), json!(url));
            entry.insert("title".to_string(), json!(title));
            let og_img = f.get("og_image").and_then(|v| v.as_str()).unwrap_or("");
            if !og_img.is_empty() {
                entry.insert("image".to_string(), json!(og_img));
            }
            sources.push(Value::Object(entry));
        }
    }
    sources
}

/// `_extract_raw_findings(findings)` — `[{url, title, summary}]` for per-source
/// display, filtering junk.
pub fn extract_raw_findings(findings: &[Value]) -> Vec<Value> {
    let mut items: Vec<Value> = Vec::new();
    for f in findings {
        let url = f.get("url").and_then(|v| v.as_str()).unwrap_or("");
        // title = f.get("title", "") or "Untitled"
        let title_raw = f.get("title").and_then(|v| v.as_str()).unwrap_or("");
        let title = if title_raw.is_empty() { "Untitled" } else { title_raw };
        let summary = f.get("summary").and_then(|v| v.as_str()).unwrap_or("");
        let evidence = f.get("evidence").and_then(|v| v.as_str()).unwrap_or("");
        // content = summary if summary else (evidence[:2000] if evidence else "")
        let content: String = if !summary.is_empty() {
            summary.to_string()
        } else if !evidence.is_empty() {
            char_slice(evidence, 2000)
        } else {
            String::new()
        };
        if !url.is_empty() && !content.is_empty() && !is_low_quality(&content) {
            items.push(json!({"url": url, "title": title, "summary": content}));
        }
    }
    items
}

/// `_format_research_report(query, full_report, stats, elapsed)` — markdown only
/// (sources / findings handled by the frontend).
pub fn format_research_report(
    _query: &str,
    full_report: &str,
    stats: &Map<String, Value>,
    elapsed: f64,
) -> String {
    let full_report = strip_thinking(Some(full_report)).unwrap_or_default();
    // stats.get('Rounds', stats.get('Findings', '?')) etc — rendered str()-like.
    let rounds = stat_or(stats, "Rounds", &["Findings"]);
    let queries = stat_or(stats, "Queries", &["Searches"]);
    let urls = stat_or(stats, "URLs", &[]);
    let summary_lines = [
        // f"{elapsed:.1f}s" — Python `:.1f`.
        format!("**Duration:** {}s", format_fixed1(elapsed)),
        format!("**Rounds:** {rounds}"),
        format!("**Queries:** {queries}"),
        format!("**URLs Analyzed:** {urls}"),
    ];
    let summary_text = summary_lines.join(" | ");

    format!(
        "---\n\n## Research Summary\n\n{summary_text}\n\n---\n\n{full_report}\n"
    )
}

// ---------------------------------------------------------------------------
// Prompt
// ---------------------------------------------------------------------------

/// `from src.deep_research import current_date_context`.
///
/// Preamble that grounds query-generation/planning LLMs in the real current
/// date. Without it the model falls back to its training-cutoff year and emits
/// queries like "best Python tutorials 2025" when the year is actually 2026.
/// System TZ-local so it matches what the user sees.
///
/// DEVIATION: Python imports this from `src.deep_research`. The ported Rust
/// `crate::src::deep_research` does not (yet) re-export it, so a faithful local
/// copy lives here (the only consumer in this module is `generate_plan`). The
/// engine itself owns its own date grounding internally; this copy matches the
/// Python `datetime.now().astimezone().strftime(...)` output byte-for-byte.
fn current_date_context() -> String {
    use chrono::Local;
    let now = Local::now();
    // %B %d, %Y  (zero-padded day, matching Python's %d).
    let long = now.format("%B %d, %Y").to_string();
    let ymd = now.format("%Y-%m-%d").to_string();
    let year = now.format("%Y").to_string();
    format!(
        "Today's date is {long} ({ymd}). \
When a search query needs a year or refers to 'latest'/'current'/\
'this year', use {year} or relative wording — never a \
year inferred from training data.\n\n"
    )
}

/// `from src.deep_research import RESEARCH_PLAN_PROMPT; RESEARCH_PLAN_PROMPT.format(question=...)`.
///
/// Embedded verbatim (the deep_research module owns the const; this is the local
/// `.format(question=...)` substitution). The literal `{{` / `}}` in the JSON
/// example collapse to single braces under Python's `str.format`, so they are
/// single braces here.
fn research_plan_prompt(question: &str) -> String {
    format!(
        "You are a research strategist. Before searching, analyze this question and create a research plan.\n\n\
**Question:** {question}\n\n\
Break this question down:\n\
1. What are the key sub-topics that need to be covered for a comprehensive answer?\n\
2. What specific data points, facts, or perspectives should we look for?\n\
3. What would a complete, high-quality answer include?\n\n\
Return a JSON object with:\n\
- \"sub_questions\": Array of 3-6 specific sub-questions to investigate\n\
- \"key_topics\": Array of key topics/angles to cover\n\
- \"success_criteria\": One sentence describing what a complete answer looks like\n\n\
Example:\n\
{{\n\
\x20 \"sub_questions\": [\"What is the cost of living in X?\", \"How is the healthcare system?\"],\n\
\x20 \"key_topics\": [\"economy\", \"healthcare\", \"safety\", \"culture\"],\n\
\x20 \"success_criteria\": \"A balanced comparison covering cost, quality of life, and practical considerations.\"\n\
}}\n"
    )
}

// ---------------------------------------------------------------------------
// Small utilities
// ---------------------------------------------------------------------------

static FENCE_OPEN_RE: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"^```(?:json)?\s*").unwrap());
static FENCE_CLOSE_RE: Lazy<regex::Regex> = Lazy::new(|| regex::Regex::new(r"\s*```$").unwrap());
// re.search(r'\{[\s\S]*\}') — greedy, first '{' to last '}'.
static JSON_OBJ_RE: Lazy<regex::Regex> =
    Lazy::new(|| regex::Regex::new(r"(?s)\{.*\}").unwrap());

/// Python truthiness for an optional `Value` (used for `data.get("consumed")`).
fn truthy(v: Option<&Value>) -> bool {
    match v {
        Some(Value::Bool(b)) => *b,
        Some(Value::Null) | None => false,
        Some(Value::Number(n)) => n.as_f64().map(|x| x != 0.0).unwrap_or(true),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// `_bounded_int(value, *, default, minimum, maximum)` — coerce a settings value
/// to an int, falling back to `default` on a non-numeric value, then clamp to
/// `[minimum, maximum]`. Mirrors the Python helper: `int(value)` with a
/// `TypeError/ValueError` fallback, then `max(minimum, min(maximum, n))`.
///
/// The input is a JSON `Value` so it can come straight from `get_setting(...)`
/// (number or numeric string) or from an explicit caller override.
fn bounded_int(value: &Value, default: i64, minimum: i64, maximum: i64) -> i64 {
    let n = match value {
        // Python `int(x)` on a float truncates toward zero.
        Value::Number(num) => num
            .as_i64()
            .or_else(|| num.as_f64().map(|f| f.trunc() as i64)),
        // `int("90")` parses; `int("90.5")` would raise ValueError, but settings
        // store plain integers — accept a leading-int parse, else fall back.
        Value::String(s) => s.trim().parse::<i64>().ok(),
        _ => None,
    };
    match n {
        Some(n) => maximum.min(minimum.max(n)),
        None => default,
    }
}

/// `int(value)` with a `TypeError/ValueError` fallback to `default` (no
/// clamping). Used for the un-clamped `raw_timeout` read that decides the
/// 0-means-unlimited branch before `bounded_int` clamps the rest.
fn parse_int_or(value: &Value, default: i64) -> i64 {
    match value {
        Value::Number(num) => num
            .as_i64()
            .or_else(|| num.as_f64().map(|f| f.trunc() as i64))
            .unwrap_or(default),
        Value::String(s) => s.trim().parse::<i64>().unwrap_or(default),
        _ => default,
    }
}

/// `_format_probe_failure(model, exc)` — turn a failed research model probe into
/// a user-facing message.
///
/// Python reads `exc.detail` / `exc.status_code` (the `fastapi.HTTPException`
/// attributes raised on the LLM upstream path) and computes
/// `err = str(detail if detail is not None else exc).strip()`. The Rust
/// `PyError::Upstream { status, detail }` carries exactly those two fields; for
/// any other error variant there is no status/detail, and `err` is the error's
/// `Display` (its friendly message), mirroring `str(exc)`.
fn format_probe_failure(model: &str, exc: &PyError) -> String {
    let (status, detail): (Option<u16>, Option<&str>) = match exc {
        PyError::Upstream { status, detail } => (Some(*status), Some(detail.as_str())),
        _ => (None, None),
    };
    // err = str(detail if detail is not None else exc).strip()
    let err_owned: String = match detail {
        Some(d) => d.trim().to_string(),
        None => exc.to_string().trim().to_string(),
    };
    let err = err_owned.as_str();

    // status in {401, 403} or "401" in err or "API key" in err or "Unauthorized" in err
    let auth_status = matches!(status, Some(401) | Some(403));
    if auth_status
        || err.contains("401")
        || err.contains("API key")
        || err.contains("Unauthorized")
    {
        return format!(
            "Model '{model}' requires an API key. Check your endpoint configuration."
        );
    }

    // if status and err: — Python truthiness; status 0 is falsy, but HTTP status
    // codes are never 0, so `Some(_)` is the truthy case.
    if status.is_some() && !err.is_empty() {
        return format!("Model '{model}' probe failed: {err}");
    }

    if !err.is_empty() {
        return format!("Cannot reach model '{model}' — {err}");
    }

    format!("Cannot reach model '{model}' — check that the endpoint is running and accessible.")
}

/// The affirmation / continuation phrases that mean "the user accepted the
/// clarifying round" rather than naming a research topic. Mirrors the Python
/// `_AFFIRMATIONS` set in `synthesize_query`.
const AFFIRMATIONS: &[&str] = &[
    "yes", "y", "yeah", "yep", "yup", "sure", "sure thing", "ok", "okay",
    "k", "kk", "go", "go ahead", "go for it", "do it", "please",
    "yes please", "sounds good", "continue", "proceed", "lets go",
    "let's go", "yes go ahead",
];

/// `_normalize(text)`: `(text or "").strip().lower().strip("!.? ")` — strip
/// surrounding whitespace, lowercase, then strip the chars `!`, `.`, `?`, and
/// space from both ends.
fn normalize_affirmation(text: &str) -> String {
    let lowered = text.trim().to_lowercase();
    lowered
        .trim_matches(|c| c == '!' || c == '.' || c == '?' || c == ' ')
        .to_string()
}

/// `normalized in _AFFIRMATIONS`.
fn is_affirmation(normalized: &str) -> bool {
    AFFIRMATIONS.contains(&normalized)
}

/// `text[:n]` — char-based (not byte-based) slice, matching Python str slicing.
fn char_slice(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// `len(text)` — char count.
fn char_len(s: &str) -> usize {
    s.chars().count()
}

/// `stats.get(primary, stats.get(fallback, '?'))` rendered like Python `f"{...}"`
/// (str() of the value, '?' if missing). Mirrors `_format_research_report`.
fn stat_or(stats: &Map<String, Value>, primary: &str, fallbacks: &[&str]) -> String {
    if let Some(v) = stats.get(primary) {
        return render_stat_value(v);
    }
    for fb in fallbacks {
        if let Some(v) = stats.get(*fb) {
            return render_stat_value(v);
        }
    }
    "?".to_string()
}

/// `f"{value}"` for a JSON-shaped stat: strings render bare, numbers without
/// quotes (Python `str()`).
fn render_stat_value(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Null => "None".to_string(),
        Value::Bool(b) => {
            if *b {
                "True".to_string()
            } else {
                "False".to_string()
            }
        }
        other => other.to_string(),
    }
}

/// Python `f"{x:.1f}"` — fixed one-decimal, banker's rounding at the half.
fn format_fixed1(x: f64) -> String {
    format!("{:.1}", x)
}

/// Python `round(x, ndigits)` — banker's (round-half-to-even) rounding.
fn round_half_even(x: f64, ndigits: i32) -> f64 {
    let factor = 10f64.powi(ndigits);
    let scaled = x * factor;
    let rounded = scaled.round();
    // f64::round is round-half-away-from-zero; correct the exact-half case to even.
    let diff = scaled - scaled.floor();
    let r = if (diff - 0.5).abs() < f64::EPSILON {
        let fl = scaled.floor();
        if (fl as i64) % 2 == 0 {
            fl
        } else {
            fl + 1.0
        }
    } else {
        rounded
    };
    r / factor
}

/// `json.loads(path.read_text())` returning an object, or `None` on any error
/// (mirrors the Python try/except-swallow pattern around the disk reads).
fn read_json_object(path: &str) -> Option<Map<String, Value>> {
    let text = std::fs::read_to_string(path).ok()?;
    match serde_json::from_str::<Value>(&text) {
        Ok(Value::Object(m)) => Some(m),
        _ => None,
    }
}

/// `path.write_text(json.dumps(data))` — compact JSON (no indent), matching the
/// Python `json.dumps` default separators.
fn write_json_compact(path: &str, data: &Map<String, Value>) -> PyResult<()> {
    let text = serde_json::to_string(&Value::Object(data.clone()))?;
    std::fs::write(path, text)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extract_sources_dedupes_and_filters() {
        let findings = vec![
            json!({"url": "https://a.com", "title": "A", "summary": "good content here"}),
            // duplicate url — skipped
            json!({"url": "https://a.com", "title": "A2", "summary": "more"}),
            // low-quality summary ("cookie") — skipped
            json!({"url": "https://b.com", "title": "B", "summary": "cookie banner"}),
            // empty url — skipped
            json!({"url": "", "title": "C", "summary": "x"}),
            // title falls back to url; og_image promoted to image
            json!({"url": "https://c.com", "summary": "real text", "og_image": "https://img"}),
        ];
        let s = extract_sources(&findings);
        assert_eq!(s.len(), 2);
        assert_eq!(s[0]["url"], json!("https://a.com"));
        assert_eq!(s[0]["title"], json!("A"));
        assert_eq!(s[1]["url"], json!("https://c.com"));
        // title fell back to url
        assert_eq!(s[1]["title"], json!("https://c.com"));
        assert_eq!(s[1]["image"], json!("https://img"));
    }

    #[test]
    fn extract_raw_findings_uses_evidence_truncation() {
        let long_evidence: String = "x".repeat(3000);
        let findings = vec![
            json!({"url": "https://a.com", "title": "A", "summary": "summ"}),
            // no summary -> evidence[:2000]
            json!({"url": "https://b.com", "evidence": long_evidence}),
            // no url -> skipped
            json!({"title": "C", "summary": "nope"}),
            // no content -> skipped
            json!({"url": "https://d.com"}),
        ];
        let items = extract_raw_findings(&findings);
        assert_eq!(items.len(), 2);
        assert_eq!(items[0]["summary"], json!("summ"));
        // title defaulted to "Untitled"
        assert_eq!(items[1]["title"], json!("Untitled"));
        assert_eq!(items[1]["summary"].as_str().unwrap().len(), 2000);
    }

    #[test]
    fn format_research_report_shape() {
        let mut stats = Map::new();
        stats.insert("Rounds".to_string(), json!(4));
        stats.insert("Queries".to_string(), json!(7));
        stats.insert("URLs".to_string(), json!(12));
        let out = format_research_report("q", "Body text.", &stats, 42.349);
        assert!(out.starts_with("---\n\n## Research Summary\n\n"));
        assert!(out.contains("**Duration:** 42.3s"));
        assert!(out.contains("**Rounds:** 4"));
        assert!(out.contains("**Queries:** 7"));
        assert!(out.contains("**URLs Analyzed:** 12"));
        assert!(out.ends_with("Body text.\n"));
    }

    #[test]
    fn format_research_report_stat_fallbacks() {
        // Rounds absent -> Findings; Queries absent -> Searches; URLs absent -> '?'.
        let mut stats = Map::new();
        stats.insert("Findings".to_string(), json!(2));
        stats.insert("Searches".to_string(), json!(5));
        let out = format_research_report("q", "B", &stats, 1.0);
        assert!(out.contains("**Rounds:** 2"));
        assert!(out.contains("**Queries:** 5"));
        assert!(out.contains("**URLs Analyzed:** ?"));
    }

    #[test]
    fn research_plan_prompt_collapses_braces() {
        let p = research_plan_prompt("Where to live?");
        assert!(p.contains("**Question:** Where to live?"));
        // The example block uses single braces (Python str.format collapses {{/}}).
        assert!(p.contains("{\n  \"sub_questions\""));
        assert!(p.contains("}\n"));
        assert!(!p.contains("{{"));
    }

    #[test]
    fn char_slice_is_unicode_safe() {
        assert_eq!(char_slice("héllo wörld", 5), "héllo");
        assert_eq!(char_len("héllo"), 5);
    }

    #[test]
    fn round_half_even_matches_python() {
        // Python round(2.5, 0)=2, round(3.5,0)=4, round(0.15,1)=0.1(binary), round(2.675,2)=2.67...
        assert_eq!(round_half_even(42.349, 1), 42.3);
        assert_eq!(round_half_even(42.35, 1), 42.4);
    }

    // --- affirmation-aware synthesis fallback --------------------------------

    #[test]
    fn normalize_affirmation_matches_python() {
        // (text or "").strip().lower().strip("!.? ")
        assert_eq!(normalize_affirmation("  YES!! "), "yes");
        assert_eq!(normalize_affirmation("Go ahead."), "go ahead");
        assert_eq!(normalize_affirmation("???"), "");
        assert_eq!(normalize_affirmation(""), "");
        // Interior punctuation is preserved (only leading/trailing stripped).
        assert_eq!(normalize_affirmation("C++"), "c++");
        assert_eq!(normalize_affirmation("yes, go ahead"), "yes, go ahead");
    }

    #[test]
    fn affirmations_classify_correctly() {
        // Bare affirmations / continuations are NOT topics.
        for a in ["yes", "Yeah!", "  OK ", "go ahead.", "let's go", "Proceed"] {
            assert!(
                is_affirmation(&normalize_affirmation(a)),
                "{a:?} should be an affirmation"
            );
        }
        // Short real topics in a clarification flow must NOT be affirmations.
        for t in ["UK", "C++", "Rust", "yes, but only the UK", "the economy"] {
            assert!(
                !is_affirmation(&normalize_affirmation(t)),
                "{t:?} should be a real topic"
            );
        }
        // Empty / punctuation-only normalizes to "" which is not in the set.
        assert!(!is_affirmation(&normalize_affirmation("???")));
    }

    // --- bounded_int / parse_int_or ------------------------------------------

    #[test]
    fn bounded_int_clamps_and_falls_back() {
        // _bounded_int(value, default=90, minimum=15, maximum=3600)
        assert_eq!(bounded_int(&json!(90), 90, 15, 3600), 90);
        assert_eq!(bounded_int(&json!(5), 90, 15, 3600), 15); // below min
        assert_eq!(bounded_int(&json!(99999), 90, 15, 3600), 3600); // above max
        // numeric string parses (settings.json stores ints, but be liberal)
        assert_eq!(bounded_int(&json!("42"), 90, 15, 3600), 42);
        // float truncates toward zero like int()
        assert_eq!(bounded_int(&json!(90.9), 90, 15, 3600), 90);
        // non-numeric -> default, THEN no further clamp needed (default in range)
        assert_eq!(bounded_int(&json!("abc"), 90, 15, 3600), 90);
        assert_eq!(bounded_int(&json!(null), 3, 1, 12), 3);
        // concurrency bounds
        assert_eq!(bounded_int(&json!(0), 3, 1, 12), 1);
        assert_eq!(bounded_int(&json!(50), 3, 1, 12), 12);
    }

    #[test]
    fn parse_int_or_matches_python_int() {
        assert_eq!(parse_int_or(&json!(1800), 1800), 1800);
        assert_eq!(parse_int_or(&json!(0), 1800), 0);
        assert_eq!(parse_int_or(&json!(-5), 1800), -5); // un-clamped
        assert_eq!(parse_int_or(&json!("600"), 1800), 600);
        assert_eq!(parse_int_or(&json!("not-an-int"), 1800), 1800);
        assert_eq!(parse_int_or(&json!(null), 1800), 1800);
        assert_eq!(parse_int_or(&json!(1800.7), 1800), 1800); // int() truncates
    }

    // --- format_probe_failure ------------------------------------------------

    #[test]
    fn probe_failure_auth_on_401_403_or_text() {
        let msg = "Model 'm' requires an API key. Check your endpoint configuration.";
        // status 401 / 403
        assert_eq!(
            format_probe_failure("m", &PyError::upstream(401, "boom")),
            msg
        );
        assert_eq!(
            format_probe_failure("m", &PyError::upstream(403, "forbidden")),
            msg
        );
        // text triggers even without a status (Other variant)
        assert_eq!(
            format_probe_failure("m", &PyError::other("HTTP 401 from upstream")),
            msg
        );
        assert_eq!(
            format_probe_failure("m", &PyError::other("missing API key")),
            msg
        );
        assert_eq!(
            format_probe_failure("m", &PyError::other("Unauthorized")),
            msg
        );
    }

    #[test]
    fn probe_failure_includes_status_and_detail() {
        // status present + detail (non-auth) -> "probe failed: {err}" using detail.
        assert_eq!(
            format_probe_failure("m", &PyError::upstream(500, "internal boom")),
            "Model 'm' probe failed: internal boom"
        );
    }

    #[test]
    fn probe_failure_includes_error_detail_without_status() {
        // No status, non-empty err -> "Cannot reach ... — {err}".
        assert_eq!(
            format_probe_failure("m", &PyError::other("connection refused")),
            "Cannot reach model 'm' — connection refused"
        );
    }

    #[test]
    fn probe_failure_generic_fallback_when_blank() {
        // Empty err, no status -> generic "check that the endpoint is running".
        assert_eq!(
            format_probe_failure("m", &PyError::other("   ")),
            "Cannot reach model 'm' — check that the endpoint is running and accessible."
        );
    }

    // --- current_date_context -------------------------------------------------

    #[test]
    fn current_date_context_shape() {
        use chrono::{Datelike, Local};
        let ctx = current_date_context();
        let year = Local::now().year().to_string();
        assert!(ctx.starts_with("Today's date is "));
        assert!(ctx.contains(&format!("use {year} or relative wording")));
        // ymd present in parentheses, real-date grounded.
        let ymd = Local::now().format("%Y-%m-%d").to_string();
        assert!(ctx.contains(&format!("({ymd})")));
        assert!(ctx.ends_with("training data.\n\n"));
    }

    // --- hard_timeout settings resolution shape -------------------------------

    #[test]
    fn start_research_args_default_hard_timeout_is_none() {
        // Python `hard_timeout: int = None` => resolve-from-settings sentinel.
        let args = StartResearchArgs::default();
        assert_eq!(args.hard_timeout, None);
        assert_eq!(args.extraction_timeout, None);
        assert_eq!(args.extraction_concurrency, None);
    }
}
