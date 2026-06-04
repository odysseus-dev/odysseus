// services/research/service.rs  <- the Python `services/research/service.py`
//! Research service — deep research with LLM-in-the-loop.
//!
//! Faithful port of `services/research/service.py`: a thin facade over the
//! canonical [`ResearchHandler`](crate::src::research_handler::ResearchHandler).
//! It exposes the Python class surface:
//!   * [`ResearchService::research`] — one-shot deep research → structured result.
//!   * [`ResearchService::start_background`] — kick off background research.
//!   * [`ResearchService::get_status`] — poll a background run.
//!   * [`ResearchService::cancel`] — cancel a background run.
//!
//! ## DOCUMENTED VIBECODE DRIFT (`research()`)
//!
//! The Python `research()` treats the handler's return value as a **dict**:
//!
//! ```python
//! result = await self.handler.call_research_service(
//!     topic, llm_endpoint, llm_model, max_time=max_time,
//!     progress_callback=on_progress)
//! ...
//! sources = [ResearchSource(url=s.get("url", ""), ...)
//!            for s in result.get("sources", [])]
//! return ResearchResult(
//!     query=topic,
//!     summary=result.get("summary", result.get("answer", "")),
//!     ...
//!     tokens_used=result.get("tokens_used", 0), ...)
//! ```
//!
//! But `call_research_service` returns a **formatted markdown report `str`**,
//! not a dict. Calling `result.get(...)` / iterating `result.get("sources")` on
//! a `str` raises `AttributeError`, so in Python `research()` **always errors**.
//! The dict-shaped parse (`.get("sources")`/`.get("summary")`/`.get("answer")`/
//! `.get("sections")`/`.get("tokens_used")`) is therefore **unreachable dead
//! code**. This is a leaf facade: the *only* reference to `ResearchService`
//! anywhere is the `services/__init__` aggregation; **no caller invokes it**
//! (verified), and **no rewire depends on it**.
//!
//! Rather than reproduce code that always raises, this port wires the facade to
//! the handler's **real** public API, preserving the observable *intent* (run
//! research on a topic, return a structured [`ResearchResult`]):
//!   * The canonical Rust `call_research_service` is **private**; the public
//!     research path is the background-task flow. So `research()` mirrors what
//!     the Python dead path *meant* to do — it runs the research through the
//!     handler's public [`start_research`](crate::src::research_handler::ResearchHandler::start_research)
//!     under a synthetic session id, polls
//!     [`get_status`](crate::src::research_handler::ResearchHandler::get_status)
//!     to completion (forwarding each progress event to the `on_progress`
//!     callback, the Python `progress_callback`), and then collects the markdown
//!     report and sources via the public getters.
//!   * The markdown report becomes [`ResearchResult::summary`] — exactly the
//!     value `result.get("summary", result.get("answer", ""))` *would* have
//!     yielded if the report were the dict's `"summary"`. The handler's source
//!     list (URL/title dicts) maps to [`ResearchSource`]s. `sections` /
//!     `tokens_used` have no source in the markdown-`str` return (the Python
//!     `.get` fallbacks were `[]` / `0`), so they stay at their defaults.
//!   * `duration_seconds` is wall-clock around the run (Python `time.time()`).
//!
//! The handler's `start_research`/`get_status`/`cancel_research` are
//! synchronous; the polling loop sleeps cooperatively (`tokio::time::sleep`) so
//! `research()` stays `async` like the Python `async def`.

use std::sync::Arc;

use indexmap::IndexMap;
use serde_json::Value;

use crate::error::PyResult;
use crate::pytime;
use crate::src::research_handler::{ResearchHandler, StartResearchArgs};

/// `@dataclass ResearchSource` — a source found during research.
///
/// ```python
/// @dataclass
/// class ResearchSource:
///     url: str
///     title: str
///     snippet: str
///     relevance: float = 0.0
/// ```
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ResearchSource {
    /// `url`.
    pub url: String,
    /// `title`.
    pub title: String,
    /// `snippet`.
    pub snippet: String,
    /// `relevance` (defaults to `0.0`).
    pub relevance: f64,
}

/// `@dataclass ResearchResult` — result of a deep research query.
///
/// ```python
/// @dataclass
/// class ResearchResult:
///     query: str
///     summary: str
///     sources: List[ResearchSource] = field(default_factory=list)
///     sections: List[str] = field(default_factory=list)
///     tokens_used: int = 0
///     duration_seconds: float = 0.0
/// ```
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ResearchResult {
    /// `query`.
    pub query: String,
    /// `summary`.
    pub summary: String,
    /// `sources` (defaults to `[]`).
    pub sources: Vec<ResearchSource>,
    /// `sections` (defaults to `[]`).
    pub sections: Vec<String>,
    /// `tokens_used` (defaults to `0`).
    pub tokens_used: i64,
    /// `duration_seconds` (defaults to `0.0`).
    pub duration_seconds: f64,
}

/// `class ResearchService` — deep research service.
///
/// ```python
/// service = ResearchService()
/// result = await service.research("quantum computing advances 2024")
/// print(result.summary)
/// ```
///
/// The Python `__init__` keeps a dead `self._active: dict = {}` field; it is
/// never read or written by any method, so it is omitted here (the handler owns
/// the real active-task registry).
pub struct ResearchService {
    /// `self.handler = ResearchHandler()`.
    ///
    /// Stored behind an `Arc` because the canonical
    /// [`ResearchHandler::start_research`](crate::src::research_handler::ResearchHandler::start_research)
    /// takes `self: &Arc<Self>` (it spawns a background task that holds a clone).
    pub handler: Arc<ResearchHandler>,
}

impl Default for ResearchService {
    fn default() -> Self {
        Self::new()
    }
}

impl ResearchService {
    /// `def __init__(self): self.handler = ResearchHandler(); self._active = {}`.
    pub fn new() -> Self {
        Self {
            handler: Arc::new(ResearchHandler::new()),
        }
    }

    /// `async def research(self, topic, llm_endpoint, llm_model, max_time=300,
    /// on_progress=None) -> ResearchResult`.
    ///
    /// Perform deep research on a topic.
    ///
    /// * `topic` — research topic/question.
    /// * `llm_endpoint` — LLM API endpoint.
    /// * `llm_model` — model to use.
    /// * `max_time` — maximum time in seconds (Python default `300`).
    /// * `on_progress` — optional progress callback (Python `progress_callback`).
    ///
    /// Returns a [`ResearchResult`] with findings.
    ///
    /// See the module-level "DOCUMENTED VIBECODE DRIFT" note: the Python body
    /// parses a dict that the handler never returns (it returns markdown `str`),
    /// so this preserves the *intent* against the real public API.
    pub async fn research(
        &self,
        topic: &str,
        llm_endpoint: &str,
        llm_model: &str,
        max_time: i64,
        mut on_progress: Option<&mut dyn FnMut(&Value)>,
    ) -> PyResult<ResearchResult> {
        // `start = time.time()`.
        let start = pytime::time();

        // The canonical `call_research_service` is private; the public research
        // path is the background-task flow. Use a synthetic session id scoped to
        // this one-shot call so it never collides with caller-managed sessions.
        let session_id = format!("research-service-{}", (start * 1_000_000.0) as i64);

        // `result = await self.handler.call_research_service(topic, llm_endpoint,
        // llm_model, max_time=max_time, progress_callback=on_progress)` — wired
        // to the real public `start_research`.
        let _ = self.handler.start_research(StartResearchArgs {
            session_id: session_id.clone(),
            query: topic.to_string(),
            llm_endpoint: llm_endpoint.to_string(),
            llm_model: llm_model.to_string(),
            max_time,
            llm_headers: IndexMap::new(),
            ..Default::default()
        });

        // Poll to completion, forwarding each progress event to `on_progress`
        // (the Python `progress_callback`, fed from `entry["progress"]`).
        loop {
            match self.handler.get_status(&session_id) {
                Some(s) => {
                    if let Some(cb) = on_progress.as_deref_mut() {
                        if let Some(progress) = s.get("progress") {
                            cb(progress);
                        }
                    }
                    let phase = s.get("status").and_then(|v| v.as_str()).unwrap_or("");
                    if matches!(phase, "done" | "error" | "cancelled") {
                        break;
                    }
                }
                // No registry entry and nothing on disk — treat as finished.
                None => break,
            }
            tokio::time::sleep(std::time::Duration::from_millis(500)).await;
        }

        // `duration = time.time() - start`.
        let duration = pytime::time() - start;

        // The handler returns a markdown report `str` (the Python
        // `result.get("summary", result.get("answer", ""))` value) plus a source
        // list (the unreachable Python `result.get("sources", [])`).
        let summary = self.handler.get_result(&session_id).unwrap_or_default();
        let raw_sources = self.handler.get_sources(&session_id).unwrap_or_default();

        // `sources = [ResearchSource(url=s.get("url", ""), title=s.get("title",
        // ""), snippet=s.get("snippet", ""), relevance=s.get("relevance", 0.0))
        // for s in result.get("sources", [])]`.
        let sources = raw_sources
            .iter()
            .map(|s| ResearchSource {
                url: s
                    .get("url")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                title: s
                    .get("title")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                snippet: s
                    .get("snippet")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                relevance: s.get("relevance").and_then(|v| v.as_f64()).unwrap_or(0.0),
            })
            .collect();

        Ok(ResearchResult {
            query: topic.to_string(),
            summary,
            sources,
            // `sections` / `tokens_used` have no source in the markdown-`str`
            // return; the Python `.get` fallbacks were `[]` / `0`.
            sections: Vec::new(),
            tokens_used: 0,
            duration_seconds: duration,
        })
    }

    /// `def start_background(self, session_id, topic, llm_endpoint, llm_model,
    /// max_time=300) -> dict`.
    ///
    /// Start research in background. Returns task info (a JSON object mirroring
    /// the Python `{"session_id", "status", "query"}` dict).
    pub fn start_background(
        &self,
        session_id: &str,
        topic: &str,
        llm_endpoint: &str,
        llm_model: &str,
        max_time: i64,
    ) -> Value {
        // `return self.handler.start_research(session_id, topic, llm_endpoint,
        // llm_model, max_time)`.
        self.handler.start_research(StartResearchArgs {
            session_id: session_id.to_string(),
            query: topic.to_string(),
            llm_endpoint: llm_endpoint.to_string(),
            llm_model: llm_model.to_string(),
            max_time,
            ..Default::default()
        })
    }

    /// `def get_status(self, session_id) -> Optional[dict]`.
    ///
    /// Get status of background research.
    pub fn get_status(&self, session_id: &str) -> Option<Value> {
        // `return self.handler.get_status(session_id)`.
        self.handler.get_status(session_id)
    }

    /// `def cancel(self, session_id) -> bool`.
    ///
    /// Cancel background research.
    pub fn cancel(&self, session_id: &str) -> bool {
        // `return self.handler.cancel_research(session_id)`.
        self.handler.cancel_research(session_id)
    }
}
