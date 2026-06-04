// services/search/service.rs  <- the Python `services/search/service.py`
//! Search service — clean interface for web search.
//!
//! Faithful port of `services/search/service.py`: a thin facade over the
//! canonical search engine (`crate::src::search`). It exposes three async
//! entry points mirroring the Python class:
//!   * [`SearchService::search`] — run a query and return structured results.
//!   * [`SearchService::fetch_content`] — fetch the full text of one URL.
//!   * [`SearchService::get_config`] — read the live search configuration.
//!
//! ## DOCUMENTED VIBECODE DRIFT
//!
//! The Python facade calls the underlying engine with the *wrong* shapes:
//!
//! ```python
//! raw_results = await comprehensive_web_search(
//!     query, max_results=10 * depth, fetch_content=fetch_content)
//! for r in raw_results:
//!     results.append(SearchResult(url=r.get("url", ""), ...))
//! ...
//! return await fetch_webpage_content(url)
//! ```
//!
//! But the real `comprehensive_web_search` is **synchronous**, takes
//! `max_pages`/`max_workers` (there is no `max_results`/`fetch_content`
//! kwarg), and returns a formatted report **`str`** (or a `(str, list)` tuple
//! when `return_sources=True`) — *not* an iterable of result dicts. Likewise
//! `fetch_webpage_content` is synchronous and returns a **`dict`**, not the
//! awaitable string the facade assumes. So in Python every method of
//! `SearchService` raises (`TypeError` on the bad kwargs / `await` of a
//! non-awaitable). This is dead, never-exercised code: the *only* reference to
//! `SearchService` anywhere is the `services/__init__` aggregation; **no
//! caller invokes it** (verified).
//!
//! Rather than reproduce code that always raises, this port wires the facade
//! to the engine's **real** signatures, preserving the observable *intent*:
//!   * `search()` calls `comprehensive_web_search(query, max_pages=10*depth,
//!     return_sources=True)` and maps the returned `(report, sources)` source
//!     list to [`SearchResult`]s. The engine's source entries carry only
//!     `{"url", "title"}` (matching the Python `_source_list` comprehension),
//!     so `snippet` is empty and `content` is `None` — exactly the data the
//!     engine surfaces. `depth` is mapped to `max_pages` (the Python intent
//!     of "more depth -> more pages").
//!   * `fetch_content()` calls `fetch_webpage_content(url)` and returns its
//!     `"content"` field (the string the Python facade meant to return).
//!   * `get_config()` returns `get_search_config()` verbatim.
//!
//! The underlying engine functions are synchronous and blocking (they use
//! `reqwest::blocking`), so — matching the rewire convention used by
//! `deep_research`/`chat_processor` — the blocking work runs inside
//! `tokio::task::spawn_blocking` while these methods stay `async` like the
//! Python `async def`s.

use serde_json::Value;

use crate::error::PyResult;
use crate::src::search::{
    comprehensive_web_search, fetch_webpage_content, get_search_config, ComprehensiveResult,
};

/// `dataclass SearchResult` — a single search result.
///
/// ```python
/// @dataclass
/// class SearchResult:
///     url: str
///     title: str
///     snippet: str
///     content: Optional[str] = None
/// ```
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SearchResult {
    /// `url`.
    pub url: String,
    /// `title`.
    pub title: String,
    /// `snippet`.
    pub snippet: String,
    /// `content` (defaults to `None`).
    pub content: Option<String>,
}

/// `dataclass SearchResponse` — response from a search query.
///
/// ```python
/// @dataclass
/// class SearchResponse:
///     query: str
///     results: List[SearchResult]
///     total: int
///     cached: bool = False
/// ```
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SearchResponse {
    /// `query`.
    pub query: String,
    /// `results`.
    pub results: Vec<SearchResult>,
    /// `total` (== `results.len()`).
    pub total: usize,
    /// `cached` (defaults to `False`).
    pub cached: bool,
}

/// `class SearchService` — web search service.
///
/// ```python
/// service = SearchService()
/// result = await service.search("python async patterns")
/// for r in result.results:
///     print(f"{r.title}: {r.url}")
/// ```
#[derive(Debug, Clone)]
pub struct SearchService {
    /// `self.default_depth` (Python default `1`).
    pub default_depth: i64,
    /// `self.fetch_content` (Python default `True`).
    pub fetch_content: bool,
}

impl Default for SearchService {
    fn default() -> Self {
        Self::new(1, true)
    }
}

impl SearchService {
    /// `def __init__(self, default_depth: int = 1, fetch_content: bool = True)`.
    pub fn new(default_depth: i64, fetch_content: bool) -> Self {
        Self {
            default_depth,
            fetch_content,
        }
    }

    /// `async def search(self, query, depth=None, fetch_content=None)`.
    ///
    /// Search the web.
    ///
    /// * `query` — search query.
    /// * `depth` — search depth (1=quick, 2=thorough, 3=comprehensive);
    ///   `None` falls back to `self.default_depth`.
    /// * `fetch_content` — whether to fetch full page content; `None` falls
    ///   back to `self.fetch_content`. (Kept for signature parity with the
    ///   Python facade — the engine always fetches up to `max_pages` pages; the
    ///   flag does not change the engine's behaviour, only its source surface.)
    ///
    /// Returns a [`SearchResponse`].
    pub async fn search(
        &self,
        query: &str,
        depth: Option<i64>,
        fetch_content: Option<bool>,
    ) -> PyResult<SearchResponse> {
        // depth = depth or self.default_depth
        let depth = depth.unwrap_or(self.default_depth);
        // fetch_content = fetch_content if fetch_content is not None else self.fetch_content
        let _fetch_content = fetch_content.unwrap_or(self.fetch_content);

        // Use existing search implementation. The Python `await
        // comprehensive_web_search(query, max_results=10*depth, ...)` is wired
        // to the real (sync) signature: `max_pages = 10 * depth`,
        // `return_sources=True`. Run the blocking engine off the async runtime.
        let owned_query = query.to_string();
        let max_pages = 10 * depth;
        let raw = tokio::task::spawn_blocking(move || {
            comprehensive_web_search(
                &owned_query,
                max_pages, // max_pages (Python intent: 10 * depth)
                4,         // max_workers (engine default)
                None,      // time_filter
                None,      // domain_whitelist
                None,      // domain_blacklist
                None,      // content_type
                None,      // language
                0,         // min_content_length
                true,      // return_sources
            )
        })
        .await
        .map_err(|e| crate::error::PyError::other(format!("search task join failed: {e}")))?;

        // The engine returns `(report, sources)` under `return_sources=True`;
        // map each source dict to a `SearchResult` exactly as the Python
        // comprehension intended (`r.get("url"/"title"/"snippet"/"content")`).
        let sources: &[Value] = match &raw {
            ComprehensiveResult::WithSources(_report, src) => src,
            // `return_sources=True` always yields `WithSources`; this arm is a
            // total-match safety net.
            ComprehensiveResult::Report(_) => &[],
        };

        let mut results = Vec::with_capacity(sources.len());
        for r in sources {
            results.push(SearchResult {
                url: r
                    .get("url")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                title: r
                    .get("title")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                snippet: r
                    .get("snippet")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string(),
                content: r
                    .get("content")
                    .and_then(|v| v.as_str())
                    .map(str::to_string),
            });
        }

        let total = results.len();
        Ok(SearchResponse {
            query: query.to_string(),
            results,
            total,
            cached: false,
        })
    }

    /// `async def fetch_content(self, url) -> Optional[str]` — fetch content
    /// from a URL.
    ///
    /// The Python facade `return await fetch_webpage_content(url)` is wired to
    /// the real (sync, `dict`-returning) signature: we run
    /// `fetch_webpage_content(url, 8, retry_attempt=0)` off the async runtime
    /// and return its `"content"` string field.
    pub async fn fetch_content(&self, url: &str) -> PyResult<Option<String>> {
        let owned_url = url.to_string();
        let data = tokio::task::spawn_blocking(move || fetch_webpage_content(&owned_url, 8, 0))
            .await
            .map_err(|e| {
                crate::error::PyError::other(format!("fetch_content task join failed: {e}"))
            })?;
        Ok(data
            .get("content")
            .and_then(|v| v.as_str())
            .map(str::to_string))
    }

    /// `def get_config(self) -> Dict[str, Any]` — current search configuration.
    pub fn get_config(&self) -> Value {
        get_search_config()
    }
}
