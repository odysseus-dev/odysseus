// services/search/mod.rs  <- the Python `services/search/__init__.py`
//! Search service — web search with SearXNG.
//!
//! Mirrors the Python `services/search/__init__.py`, which re-exports the
//! low-level search surface from the sibling `core`/`content`/`providers`/
//! `analytics` modules *plus* the high-level `SearchService` facade from
//! `.service`:
//!
//! ```python
//! from .core import (comprehensive_web_search, get_search_config,
//!     invalidate_search_cache, searxng_search_results, update_search_config)
//! from .content import fetch_webpage_content
//! from .providers import searxng_search, searxng_search_api, PROVIDER_INFO
//! from .analytics import (get_search_stats, SearchEngineError, NetworkError,
//!     ParseError, RateLimitError)
//! from .service import SearchService, SearchResult, SearchResponse
//! ```
//!
//! ## Cardinal dup re-export rule
//!
//! The `services/search/{analytics,cache,query,ranking}.py` modules are
//! **byte-identical** to `src/search/*` (verified with `diff`), and the
//! `services/search/{core,content,providers}.py` modules are likewise the same
//! engine. Per the cardinal rule, the canonical engine lives in
//! `crate::src::search`; this package is a **thin re-export** of it (rather
//! than a re-port) and adds only the genuinely-unique `service` facade.
//!
//! `pub use crate::src::search::*` brings the whole canonical surface into
//! `crate::services::search`, so `crate::services::search::{
//! comprehensive_web_search, fetch_webpage_content, get_search_config,
//! get_search_stats, invalidate_search_cache, searxng_search,
//! searxng_search_api, searxng_search_results, update_search_config,
//! PROVIDER_INFO, SearchEngineError, NetworkError, ParseError, RateLimitError}`
//! resolve exactly like the Python `__init__` (the "low-level functions, for
//! backwards compat" half of `__all__`).

// Re-export the canonical search engine (analytics/cache/query/ranking plus
// core/content/providers — all always compiled, no cargo feature flags). This is
// the `from .core/.content/.providers/.analytics import …` half of the Python
// `__init__` — see the canonical `crate::src::search` package.
pub use crate::src::search::*;

// The genuinely-unique service-layer facade.
pub mod service;
pub use service::{SearchResponse, SearchResult, SearchService};
