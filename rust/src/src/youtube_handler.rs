// src/youtube_handler.rs  <- src/youtube_handler.py
//! Provenance alias for the YouTube handler.
//!
//! `youtube_handler.py` was ported as [`crate::src::youtube_min`] (the pure
//! slice — `is_youtube_url`, `extract_youtube_id`,
//! `format_transcript_for_context`, `format_comments_for_context`,
//! `YOUTUBE_INSTRUCTION_PROMPT` — plus the honest `extract_transcript_async` /
//! `fetch_youtube_comments` stubs). This module re-exports that port verbatim so
//! the original Python module name resolves to its faithful Rust counterpart.
//!
//! The network halves stay HONEST STUBS in `youtube_min` (the
//! `youtube-transcript-api` transcript fetch and the `yt-dlp` comment fetch
//! depend on reverse-engineered, rotting YouTube internals — a reqwest reimpl
//! would be fabrication, not a port). `init_youtube` / `_find_ytdlp` are
//! intentionally omitted (they only lazily import the unported transcript API
//! and locate the un-invoked `yt-dlp` binary).

pub use crate::src::youtube_min::*;
