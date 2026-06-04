// services/docs/mod.rs  <- the Python `services/docs/__init__.py`
//! Docs service — personal document RAG with ChromaDB.
//!
//! From the Python `__init__` docstring:
//! > Thin facade: `DocsService` lives here, `RAGManager`/`VectorRAG` are
//! > re-exported from the canonical implementations in `src/`.
//!
//! ## Cardinal dup re-export rule
//!
//! `RAGManager` and `VectorRAG` are NOT re-ported here: the canonical
//! implementations already live in `crate::src::rag_manager` /
//! `crate::src::rag_vector` (ported `all(web, db)`). This module re-exports them
//! to mirror the Python `__init__` surface and authors only the genuinely-unique
//! `DocsService` facade.
//!
//! Feature gate: `all(web, db)` — `RAGManager` owns a `VectorRAG`, which needs the
//! HTTP embedding client (`web`) and the rusqlite vector store (`db`).

pub mod service;

pub use service::{DocChunk, DocsService, IndexResult};

// Re-exports mirroring the Python `__init__` `__all__`
// (`from src.rag_manager import RAGManager`, `from src.rag_vector import VectorRAG`).
pub use crate::src::rag_manager::RAGManager;
pub use crate::src::rag_vector::VectorRAG;
