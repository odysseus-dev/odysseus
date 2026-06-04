// services/research/mod.rs  <- the Python `services/research/__init__.py`
//! Research service — deep research with LLM-in-the-loop.
//!
//! Mirrors the Python `services/research/__init__.py`:
//!
//! ```python
//! """Research service — deep research with LLM-in-the-loop."""
//!
//! from .service import ResearchService, ResearchResult, ResearchSource
//! from .research_handler import ResearchHandler
//!
//! __all__ = [
//!     "ResearchService",
//!     "ResearchResult",
//!     "ResearchSource",
//!     "ResearchHandler",
//! ]
//! ```
//!
//! ## Cardinal dup re-export rule (`research_handler`)
//!
//! `services/research/research_handler.py` is **not** unique content: it is a
//! 463-LOC *older subset* of the already-ported 801-LOC `src/research_handler.py`
//! twin (the canonical [`crate::src::research_handler`]). The services copy is
//! an earlier snapshot that diverges from the canonical superset in three
//! observable ways:
//!   * it **lacks** `_probe_endpoint` (the canonical twin probes the endpoint
//!     before committing to a long run);
//!   * it **lacks** prior-report / continuation support (the `prior_report` /
//!     `prior_findings` / `prior_urls` "continue research" path) and the
//!     `raw_report` result field;
//!   * its `get_status`/`get_result` path **deletes** completed result files,
//!     whereas the canonical twin *consumes* them (the `"consumed"` sentinel)
//!     and keeps them on disk.
//!
//! Per the cardinal rule, the services package re-exports the **canonical
//! superset** twin rather than re-porting the stale subset:
//!
//! ```rust,ignore
//! pub use crate::src::research_handler::ResearchHandler;
//! ```
//!
//! The genuinely-unique content of `services/research/` is the `service`
//! facade (`ResearchService`/`ResearchResult`/`ResearchSource`), which *is*
//! ported — see [`service`].

// The genuinely-unique service-layer facade.
pub mod service;
pub use service::{ResearchResult, ResearchService, ResearchSource};

// Re-export the canonical `ResearchHandler` superset twin (see the
// cardinal-dup note above). NOT a re-port of the stale 463-LOC services copy.
pub use crate::src::research_handler::ResearchHandler;
