// services/mod.rs  <- the Python `services/__init__.py`
//! Mirrors the Python `services/` package.
//!
//! From `services/__init__.py`:
//! > Service layer — plug-in capabilities for the chat core.
//! >
//! > Each service:
//! > - Does one thing well
//! > - Exposes a clean async interface
//! > - Can run in-process or as a standalone HTTP service
//!
//! ## Cardinal dup re-export rule
//!
//! The Python `services/` tree is partly *vibecoded*: several modules are
//! copy-paste duplicates of their canonical `src/` twins rather than a
//! deliberate split. Where a `services/X.py` byte-duplicates an already-ported
//! `src/` module (verified with `diff`), the corresponding
//! `crate::services::X` is a **thin re-export** of the canonical
//! `crate::src::X` — it is NOT re-ported. Confirmed duplicates this round:
//!   * `services/search/{analytics,cache,query,ranking}.py` == `src/search/*`
//!   * `services/memory/memory_vector.py` == `src/memory_vector.py`
//!   * `services/youtube/youtube_handler.py` == `src/youtube_handler.py`
//!
//! Two more diverge (older copies / subsets) and are re-exported to their
//! canonical superset twin with a documented-divergence note in the submodule:
//!   * `services/memory/memory.py` (older copy; dead `claim_ownerless`, no
//!     `uses`/`increment_uses`) -> `crate::src::memory::MemoryManager`
//!   * `services/research/research_handler.py` (463-LOC subset of the ported
//!     801-LOC twin) -> `crate::src::research_handler::ResearchHandler`
//!
//! Three vibecode-broken facades are handled explicitly: `SearchService`
//! (wrong kwargs on a sync `String`-returning fn) and `ResearchService`
//! (treats a `String` return as a dict) are ported against the REAL
//! underlying signatures with documented drift; `MemoryService` is OMITTED
//! (zero callers; it calls methods that do not exist on the canonical API) —
//! see `crate::services::memory` for the rationale.
//!
//! ## Feature gates
//!
//! The Python `__init__` import path has no notion of build features; the
//! Rust split below mirrors what each submodule actually touches:
//!   * default-buildable: `memory` (skill scan/format) + `hwfit` (fit math) +
//!     `youtube` (pure re-export)
//!   * `web`: `search` / `research` / `tts` / `stt` / `shell` (HTTP + LLM)
//!   * `web` + `db`: `docs` (RAG owns web embeddings + db vector store)
//! The web/db-only parts *within* `memory` are gated individually inside
//! `memory/mod.rs`.

// Default-buildable packages (their web/db-only parts are gated internally).
pub mod hwfit;
pub mod memory;
pub mod youtube;

// HTTP / LLM service surfaces.
pub mod research;
pub mod search;
pub mod shell;
pub mod stt;
pub mod tts;

// RAG-backed docs facade: needs web (embeddings) + db (vector store).
pub mod docs;
