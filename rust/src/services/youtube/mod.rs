// services/youtube/mod.rs  <- the Python `services/youtube/__init__.py`
//! Mirrors `services/youtube/__init__.py` ("YouTube service — transcript
//! extraction"), which re-exports `services/youtube/youtube_handler.py`.
//!
//! ## Cardinal dup re-export rule
//!
//! `services/youtube/youtube_handler.py` is **byte-identical** to
//! `src/youtube_handler.py` (verified with `diff`: empty output). The canonical
//! `src` twin is already ported as [`crate::src::youtube_min`] (the pure slice)
//! and aliased by [`crate::src::youtube_handler`]. Per the cardinal dup rule,
//! `crate::services::youtube` is a **thin re-export** of that canonical port —
//! it is NOT re-ported.
//!
//! This brings across the pure, portable surface verbatim:
//!   * [`YOUTUBE_INSTRUCTION_PROMPT`] — the structured-breakdown system prompt
//!   * [`is_youtube_url`] / [`extract_youtube_id`] — URL predicate + id parser
//!   * [`format_transcript_for_context`] / [`format_comments_for_context`] —
//!     LLM-context formatters
//!   * [`extract_transcript_async`] / [`fetch_youtube_comments`] — the network
//!     halves, which stay HONEST STUBS in `youtube_min` (the
//!     `youtube-transcript-api` transcript fetch and the `yt-dlp` comment fetch
//!     depend on reverse-engineered, rotting YouTube internals — a reqwest
//!     reimpl would be fabrication, not a port). They are NOT un-stubbed this
//!     round.
//!
//! ## `init_youtube` surface parity
//!
//! The Python `__all__` also lists `init_youtube`, but in Python that function
//! only *lazily imports and caches* the `youtube_transcript_api` module into a
//! pair of module globals (`YouTubeTranscriptApi`, `YOUTUBE_AVAILABLE`); since
//! the transcript API is an HONEST STUB here, there is nothing to import or
//! cache, so there is no behavioral equivalent. A no-op [`init_youtube`] is
//! provided below purely to match the `__init__` export list. (`_find_ytdlp`
//! is not part of `__all__` and only locates the un-invoked `yt-dlp` binary, so
//! it is intentionally omitted.)

#[doc(inline)]
pub use crate::src::youtube_handler::*;

/// `services.youtube.init_youtube()` — no-op surface-parity shim.
///
/// In Python `init_youtube()` performs a guarded `import youtube_transcript_api`
/// and caches the module + an availability flag into globals consumed by
/// `extract_transcript_async`. Because that transcript fetch is an HONEST STUB
/// in [`crate::src::youtube_min`] (the third-party API is genuinely un-portable),
/// there is no module to import and no global to set, so the Rust equivalent is
/// a no-op. It exists only so the `services::youtube` re-export surface mirrors
/// the Python `__all__` (which lists `init_youtube`).
pub fn init_youtube() {}
