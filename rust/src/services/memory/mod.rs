// services/memory/mod.rs  <- the Python `services/memory/__init__.py`
//! `services::memory` package root.
//!
//! Mirrors the Python `services/memory/__init__.py`:
//! ```python
//! """Memory service — persistent memory storage and retrieval."""
//!
//! from .service import MemoryService, Memory, MemorySearchResult
//! from .memory import MemoryManager
//! from .memory_vector import MemoryVectorStore
//!
//! __all__ = [
//!     "MemoryService", "Memory", "MemorySearchResult",
//!     "MemoryManager", "MemoryVectorStore",
//! ]
//! ```
//!
//! ## Submodules
//!
//! The genuinely-unique content of `services/memory/` lives in four submodules
//! that have no `src/` twin and are ported for real:
//!   * [`skill_format`] — pure mini-YAML / Markdown `Skill` (de)serialization
//!     (zero IO; default build).
//!   * [`skills`] — the disk-backed `SkillsManager` (`SKILL.md` scan +
//!     `_usage.json` sidecar; no async/HTTP/DB; default build). **REWIRE
//!     TARGET** for `app_initializer`'s `ChatProcessor` skills index.
//!   * [`skill_extractor`] — async LLM skill auto-extraction (needs the LLM
//!     HTTP path; `web` build).
//!   * [`memory_extractor`] — async LLM memory extraction + audit, with vector
//!     dedup/rebuild (needs LLM HTTP + the SQLite vector store; `web` + `db`).
//!
//! ## Re-exports (cardinal dup rule)
//!
//! Two `services/memory/` modules are NOT re-ported — they duplicate (or are
//! older copies of) their canonical `src/` twins, so this package re-exports
//! the `crate::src::*` originals (verified with `diff`):
//!
//! * `services/memory/memory_vector.py` is **byte-identical** to
//!   `src/memory_vector.py` -> [`MemoryVectorStore`] re-exports
//!   `crate::src::memory_vector::MemoryVectorStore`. (`web`-gated: the
//!   canonical twin drives the SQLite vector store.)
//!
//! * `services/memory/memory.py` is a **divergent OLDER copy** of
//!   `src/memory.py`. It carries a dead `claim_ownerless` migration helper
//!   (zero callers) and is MISSING the canonical superset's `uses` accounting
//!   (`increment_uses`, the `uses=0` default in `add_entry`/`_validate`). Per
//!   the cardinal rule we re-export the canonical *superset* twin
//!   `crate::src::memory::MemoryManager`; `claim_ownerless` is not re-ported
//!   anywhere (it has no callers).
//!
//! ## Omitted `MemoryService` facade
//!
//! `services/memory/service.py` exports `MemoryService` / `Memory` /
//! `MemorySearchResult`. It is **OMITTED** from this port — deliberately, not
//! by oversight — because it is vibecoded against an API that does not exist:
//!
//! * `MemoryService.remember` calls `manager.add_memory(entry)` and
//!   `vector_store.add(text, {meta})`; `recall` calls
//!   `manager.search_memories(query, limit=...)` and `vector_store.search(query,
//!   k=...)`; `get_all` calls `manager.get_memories(limit=...)`; `delete` calls
//!   `manager.delete_memory(memory_id)`.
//! * The canonical `MemoryManager` exposes none of those names — its surface is
//!   `add_entry(text, source, category, owner)`, `load`/`load_all`,
//!   `find_duplicates`, `get_relevant_memories`, `increment_uses`, ... There is
//!   no `add_memory`/`search_memories`/`get_memories`/`delete_memory`.
//! * The canonical `MemoryVectorStore` is `add(memory_id, text)` /
//!   `search(query, k)`, not the facade's `add(text, {meta})`.
//!
//! Every `MemoryService` method would therefore raise `AttributeError` at the
//! first call in Python. It also has **zero callers** anywhere in the
//! codebase (only `services/memory/__init__` aggregates it). Re-implementing it
//! would mean fabricating behavior against a non-existent API, which the
//! port's cardinal rule forbids (never a fake success). If `__init__`-surface
//! parity is ever demanded, the honest fallback is inert `Memory` /
//! `MemorySearchResult` structs plus a `MemoryService` that returns a
//! `NotAvailable` error from every method — never a faked store.

// Pure, default-buildable submodules.
pub mod skill_format;
pub mod skills;

// Async LLM skill auto-extraction needs the LLM HTTP path.
pub mod skill_extractor;

// Async memory extraction + audit needs the LLM HTTP path *and* the SQLite
// vector store (for per-fact dedup / rebuild).
pub mod memory_extractor;

// `from .memory import MemoryManager` — re-export the canonical superset twin
// (services/memory/memory.py is a divergent older copy; see module docs).
pub use crate::src::memory::MemoryManager;

// `from .memory_vector import MemoryVectorStore` — re-export the byte-identical
// canonical twin (always compiled: it drives the SQLite vector store).
pub use crate::src::memory_vector::MemoryVectorStore;
