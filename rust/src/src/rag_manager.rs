// src/rag_manager.rs  <- src/rag_manager.py
//! A thin wrapper around `VectorRAG` for backward compatibility.
//!
//! ## Port classification — PORT_VIA_DB (web)
//!
//! The Python `RAGManager` is a backward-compatibility shim: it constructs a
//! `VectorRAG(persist_directory=...)` and delegates every method straight through.
//! This port reproduces that exactly — `RAGManager` owns a `VectorRAG` *by value*
//! and forwards each call.
//!
//! ### Sync -> async
//!
//! The ported `VectorRAG` is async (its constructor does an HTTP embedding
//! dimension probe; `search`/`retrieve`/`add_*`/`index_personal_documents` await
//! the embedding HTTP client). So `RAGManager::new` and the IO-bound delegating
//! methods are `async`; `rebuild_index` / `get_stats` stay sync (they are sync on
//! `VectorRAG`).
//!
//! ### Richer Rust signatures, reconciled
//!
//! The ported `VectorRAG` methods take a couple of extra optional arguments the
//! Python `RAGManager` did not thread through (`search`'s `owner`,
//! `index_personal_documents`'s `file_extensions` / `owner`). The Python shim
//! always called the defaults, so this wrapper passes `None` for them — preserving
//! the Python `RAGManager` behaviour exactly. `search` maps each `SearchHit` to its
//! Python-dict shape via `SearchHit::to_value()` to reproduce the
//! `List[Dict[str, Any]]` return type.
//!
//! ### Status
//!
//! Dead / backward-compatibility code: there is no in-tree caller of `RAGManager`
//! (the live RAG entrypoints are `rag_singleton::get_rag_manager` — disabled — and
//! `tool_index`'s direct `chroma_client` use). Ported faithfully for completeness.
//!
//! Feature gate: `web` (owns a `VectorRAG`, which needs HTTP embeddings + the
//! rusqlite vector store).

use serde_json::Value;

use crate::pylog as logger;
use crate::src::rag_vector::VectorRAG;

/// A manager class that wraps `VectorRAG` for backward compatibility.
///
/// Most methods delegate directly to `VectorRAG` (mirrors the Python `RAGManager`).
pub struct RAGManager {
    /// The wrapped engine (`self.vector_rag`).
    pub vector_rag: VectorRAG,
}

impl RAGManager {
    /// `RAGManager.__init__(self, persist_directory="data/chroma")`.
    ///
    /// Builds the wrapped `VectorRAG`. Async because the `VectorRAG` constructor
    /// runs `_initialize_system` (an HTTP embedding dimension probe). The Python
    /// default `persist_directory` is `"data/chroma"`; callers pass it explicitly
    /// here.
    pub async fn new(persist_directory: &str) -> Self {
        let vector_rag = VectorRAG::new(persist_directory).await;
        logger::info("RAGManager initialized as wrapper for VectorRAG");
        RAGManager { vector_rag }
    }

    /// `search(self, query, k=5) -> List[Dict[str, Any]]` — delegates to VectorRAG.
    ///
    /// The Python shim called `self.vector_rag.search(query, k)` (no `owner`); we
    /// pass `owner=None`, then render each `SearchHit` as its Python dict.
    pub async fn search(&self, query: &str, k: i64) -> Vec<Value> {
        self.vector_rag
            .search(query, k, None)
            .await
            .iter()
            .map(|hit| hit.to_value())
            .collect()
    }

    /// `index_personal_documents(self, directory) -> dict` — delegates to VectorRAG.
    ///
    /// The Python shim passed only `directory`; we pass `file_extensions=None` /
    /// `owner=None` (the `VectorRAG` defaults the Python shim relied on).
    pub async fn index_personal_documents(&self, directory: &str) -> Value {
        self.vector_rag
            .index_personal_documents(directory, None, None)
            .await
    }

    /// `retrieve(self, query, k=5) -> List[str]` — delegates to VectorRAG.
    pub async fn retrieve(&self, query: &str, k: i64) -> Vec<String> {
        self.vector_rag.retrieve(query, k).await
    }

    /// `rebuild_index(self) -> bool` — delegates to VectorRAG.
    ///
    /// `&mut self` because `VectorRAG::rebuild_index` mutates `healthy`/the
    /// collection handle on a clean rebuild.
    pub fn rebuild_index(&mut self) -> bool {
        self.vector_rag.rebuild_index()
    }

    /// `get_stats(self) -> Dict[str, Any]` — delegates to VectorRAG.
    pub fn get_stats(&self) -> Value {
        self.vector_rag.get_stats()
    }

    /// `add_document(self, text, metadata) -> bool` — delegates to VectorRAG.
    pub async fn add_document(&self, text: &str, metadata: &Value) -> bool {
        self.vector_rag.add_document(text, metadata).await
    }

    /// `add_documents_batch(self, docs) -> Dict[str, Any]` — delegates to VectorRAG.
    ///
    /// `docs` is the Python `List[tuple]` of `(text, metadata)` pairs.
    pub async fn add_documents_batch(&self, docs: &[(String, Value)]) -> Value {
        self.vector_rag.add_documents_batch(docs).await
    }
}
