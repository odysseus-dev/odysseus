// src/search/mod.rs  <- src/search/__init__.py
//! Search package — drop-in replacement for the monolithic search_engine module.
//!
//! Mirrors the Python `__init__`, which re-exports the public surface from
//! `core`/`content`/`providers`/`analytics`. The pure-logic members
//! (analytics/cache/query/ranking) build on the default profile; `core`,
//! `providers`, and `content` touch the network (`reqwest`/`scraper`) and are
//! `web`-gated.

pub mod analytics;
pub mod cache;
pub mod query;
pub mod ranking;

pub mod content;
pub mod core;
pub mod providers;

// Re-export the public surface mirroring the Python `__init__` (`from .core
// import ...`, `from .content import ...`, `from .providers import ...`,
// `from .analytics import ...`). All members are always compiled and
// re-exported; the crate has no cargo feature flags.

pub use analytics::{get_search_stats, NetworkError, ParseError, RateLimitError, SearchEngineError};

pub use core::{
    comprehensive_web_search, get_search_config, invalidate_search_cache, searxng_search_results,
    update_search_config, ComprehensiveResult, _build_provider_chain, _call_provider,
};
pub use content::fetch_webpage_content;
pub use providers::{
    searxng_search, searxng_search_api, PROVIDER_INFO, _get_result_count, _get_search_settings,
};
