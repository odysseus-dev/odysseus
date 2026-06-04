// src/chroma_client.rs  <- src/chroma_client.py
//! Singleton ChromaDB client facade — the backend-selecting front door for the
//! vector store.
//!
//! ## Port classification — PORT_VIA_DB (web)
//!
//! The Python module is a thin singleton around `chromadb.HttpClient`: it connects
//! to a ChromaDB instance running as a standalone service (`CHROMADB_HOST` /
//! `CHROMADB_PORT`), runs a `heartbeat()` health check, and raises a `RuntimeError`
//! with an install hint when the optional `chromadb` package is missing.
//!
//! ## Two coexisting backends (selection lives in `vector_store`)
//!
//! The Rust port keeps the deliberate ChromaDB->SQLite swap as the **default**
//! backend (see [`crate::src::vector_store`]) while ALSO offering a real
//! ChromaDB-over-HTTP backend. The choice is made by
//! [`crate::src::vector_store::select_backend`] (env-driven:
//! `ODYSSEUS_VECTOR_BACKEND=chroma`, or an explicitly set `CHROMADB_HOST`/
//! `CHROMADB_PORT`, selects the HTTP backend; otherwise the SQLite swap stays the
//! default — byte-for-byte identical to today when no relevant env is set).
//!
//! This facade is a **zero-sized `ChromaClient`** whose methods delegate to the
//! now selection-aware `vector_store` entry points:
//!   * `get_or_create_collection` -> `VectorCollection::get_or_create(name)`
//!     (constructs the SQLite or ChromaHttp variant per `select_backend`);
//!   * `delete_collection` -> `vector_store::delete_collection(name)`
//!     (likewise selection-aware, because rag_vector/memory_vector call this free
//!     fn directly, not through the facade);
//!   * `heartbeat()` -> `vector_store::chroma_heartbeat()` (a real best-effort
//!     ping when the ChromaHttp backend is selected; a no-op for SQLite, which is
//!     local and has no service to ping — preserving the Python init's health
//!     check semantics without changing this facade's signature);
//!   * `reset_client()` -> clears the cached backend selection in `vector_store`
//!     so a config change (the embedding-routes reset hook) re-resolves the
//!     backend on the next call.
//!
//! The ChromaDB `metadata` / `hnsw:space` arguments are accepted-and-ignored here
//! (the SQLite backend hardwires cosine; the ChromaHttp backend sets
//! `hnsw:space=cosine` itself in `get_or_create`).
//!
//! ### Documented intentional divergence (NOT a fake)
//!
//! The Python `RuntimeError("ChromaDB integration is not installed …")` path is
//! **dead code** in the Rust port: both backends are compiled in (SQLite is a hard
//! dependency of the `web`/`db` builds; the HTTP client is plain reqwest). There
//! is no optional-pip-package install hint to raise.
//!
//! Feature gate: `web` (the vector store pulls in the rusqlite `db` layer and sits
//! with the vector cohort).

use crate::error::PyResult;
use crate::src::vector_store::{self, VectorCollection};

/// The singleton "client" — a zero-sized facade.
///
/// Mirrors the Python module-level `_client` (a `chromadb.HttpClient`). It carries
/// no state: each backend manages its own connection (the SQLite variant opens a
/// short-lived rusqlite connection per call; the ChromaHttp variant shares a
/// module-level `reqwest::blocking::Client`). `Copy`/`Clone` so the
/// `get_chroma_client()` factory can hand out as many handles as ChromaDB's shared
/// singleton implied, at zero cost.
#[derive(Debug, Clone, Copy, Default)]
pub struct ChromaClient;

impl ChromaClient {
    /// `client.get_or_create_collection(name=..., metadata=...)`.
    ///
    /// Delegates to `VectorCollection::get_or_create(name)`, which is the backend
    /// SELECTION POINT: it reads `select_backend()` and constructs either the
    /// SQLite or the ChromaHttp variant. Any `metadata` / `hnsw:space` argument
    /// ChromaDB callers pass is accepted-and-ignored (cosine is fixed in both
    /// backends). Returns `Err` only if the backend cannot be reached (the analogue
    /// of ChromaDB's connection failure).
    pub fn get_or_create_collection(&self, name: &str) -> PyResult<VectorCollection> {
        VectorCollection::get_or_create(name)
    }

    /// `client.delete_collection(name=...)`.
    ///
    /// Delegates to the selection-aware `vector_store::delete_collection`, returning
    /// the number of vectors removed. (ChromaDB's `delete_collection` returns `None`;
    /// the Rust store reports an informational row/id count, which callers may
    /// ignore.)
    pub fn delete_collection(&self, name: &str) -> PyResult<i64> {
        vector_store::delete_collection(name)
    }

    /// `_client.heartbeat()` — the ChromaDB service health check.
    ///
    /// A real best-effort ping when the ChromaHttp backend is selected (delegating
    /// to `vector_store::chroma_heartbeat()`, which pings `GET /api/v2/heartbeat`),
    /// and a no-op when the SQLite backend is selected (local, always-available,
    /// no service to ping). Mirrors the Python init's `_client.heartbeat()` health
    /// check while keeping this facade's signature unchanged.
    ///
    /// Best-effort: a failed ping is logged inside `chroma_heartbeat` and swallowed
    /// here (the Python health check raised, but the Rust call sites only use it as
    /// a liveness probe and degrade gracefully — never a hard crash on a transient
    /// Chroma hiccup).
    pub fn heartbeat(&self) {
        let _ = vector_store::chroma_heartbeat();
    }
}

/// `get_chroma_client()` — get or create the singleton client.
///
/// In Python this lazily builds a `chromadb.HttpClient` (and raises if `chromadb`
/// is not installed). Here it simply returns the zero-sized facade; the missing-
/// dependency `RuntimeError` branch is dead (both backends are compiled in — see
/// the module docstring). The actual backend is resolved per call inside
/// `vector_store` (`select_backend`), so a single facade serves both.
pub fn get_chroma_client() -> ChromaClient {
    ChromaClient
}

/// `reset_client()` — reset the singleton (e.g. after a config change).
///
/// The facade itself is stateless, but `vector_store` caches the resolved backend
/// selection (and the ChromaHttp collection-id resolution). This clears that cache
/// so the next `get_or_create_collection` / `delete_collection` / `heartbeat`
/// re-reads the environment and re-resolves the backend — matching the Python
/// `global _client = None` (which forced a fresh `HttpClient` on next use) and the
/// embedding-routes reset hook's intent.
pub fn reset_client() {
    vector_store::reset_backend_selection();
}
