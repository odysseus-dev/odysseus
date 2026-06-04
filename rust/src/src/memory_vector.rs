// src/memory_vector.rs  <- src/memory_vector.py
//! SQLite-backed vector store for memory entries.
//!
//! Shares the `EmbeddingClient` with RAG to save memory. Stores pre-computed
//! embeddings (the vector store does not manage embedding).
//!
//! ## Port classification — PORT_VIA_DB (web)
//!
//! The Python `MemoryVectorStore` drives a ChromaDB *collection* object
//! (`"odysseus_memories"`). Per the project infra decision (the deliberate
//! ChromaDB->SQLite swap), this port builds on `crate::src::vector_store`
//! (the `VectorCollection` facade over the shared `vectors` table) and on
//! `crate::src::embeddings::EmbeddingClient`. It is a pure backend swap: the
//! add/remove/count/search/find_similar/rebuild surface is faithful, including
//! the `round(1 - distance, 4)` similarity score and the `0.92` near-duplicate
//! threshold.
//!
//! ### Documented deviations
//!
//! * The embedding client is injected as `Arc<EmbeddingClient>` (mirroring
//!   `app_initializer` sharing one instance across rag/memory/personal-docs) or
//!   lazily built via `get_embedding_client()`, exactly as the Python `__init__`
//!   accepts `embedding_model=None` and falls back to the factory.
//! * `round(x, 4)` uses Python banker's rounding; the Rust port rounds
//!   half-away-from-zero. Cosmetic only — it affects the displayed `score`
//!   digits, not which memory ids rank.
//! * `data_dir` is accepted for signature parity (the Python stores it but never
//!   uses it — ChromaDB persistence is handled by the client, and here by the
//!   shared `vectors` table); it is kept as a field for faithfulness.
//!
//! Feature gate: `web` (HTTP embeddings + the rusqlite-backed vector store).

use std::sync::Arc;

use serde_json::{json, Map, Value};

use crate::error::PyResult;
use crate::pylog as logger;
use crate::src::embeddings::{get_embedding_client, EmbeddingClient};
use crate::src::vector_store::{self, VectorCollection};

/// `COLLECTION_NAME = "odysseus_memories"` (a class attribute in the Python).
pub const COLLECTION_NAME: &str = "odysseus_memories";

/// `round(x, 4)` — half-away-from-zero (documented banker's-rounding drift).
fn round4(x: f64) -> f64 {
    (x * 10_000.0).round() / 10_000.0
}

/// Process-global handle to the live `MemoryVectorStore` (the Python module-level
/// `_memory_vector`). Set at startup from `AppState.memory_vector`; read by the
/// agent-tool path (`ai_interaction::do_manage_memory`) which — unlike the HTTP
/// routes — has no `AppState`. `None` when the vector store is degraded/absent.
static MEMORY_VECTOR: once_cell::sync::Lazy<std::sync::RwLock<Option<Arc<MemoryVectorStore>>>> =
    once_cell::sync::Lazy::new(|| std::sync::RwLock::new(None));

/// Install the global `MemoryVectorStore` (called once at app startup).
pub fn set_memory_vector(mv: Option<Arc<MemoryVectorStore>>) {
    *MEMORY_VECTOR.write().unwrap() = mv;
}

/// Read the global `MemoryVectorStore` (the `if _memory_vector:` check).
pub fn memory_vector() -> Option<Arc<MemoryVectorStore>> {
    MEMORY_VECTOR.read().unwrap().clone()
}

/// `{"source": "memory"}` — the fixed metadata every memory vector carries.
fn memory_meta() -> Value {
    let mut m = Map::new();
    m.insert("source".into(), json!("memory"));
    Value::Object(m)
}

/// One semantic-search result (`{"memory_id": str, "score": float}`).
#[derive(Debug, Clone, PartialEq)]
pub struct MemoryHit {
    pub memory_id: String,
    pub score: f64,
}

impl MemoryHit {
    /// Render as the Python `dict` (a `serde_json::Value` object) for callers
    /// that consume the JSON shape (`hit["memory_id"]`, `hit["score"]`).
    pub fn to_value(&self) -> Value {
        json!({ "memory_id": self.memory_id, "score": self.score })
    }
}

/// Vector index over memory entries for semantic retrieval.
///
/// Mirrors the Python `MemoryVectorStore`. `collection`/`model` are `Option` so
/// the `healthy` flag (`self._healthy`) ports faithfully; the embedding client is
/// shared as `Arc<EmbeddingClient>`.
///
/// `Clone` is cheap and observationally transparent: every field is either a
/// `String`, a shared `Arc<EmbeddingClient>`, or a `VectorCollection` handle
/// (`{ name }`). All real state lives in the shared SQLite `vectors` table, so a
/// cloned store reads/writes the SAME backing collection. This lets a route that
/// holds an `Arc<MemoryVectorStore>` (immutable, shared) hand an owned
/// `&mut Option<MemoryVectorStore>` to `audit_memories` (whose `rebuild` needs
/// `&mut self` to reset the collection handle) — the Python passes the one shared
/// object, and rebuilding the DB-backed collection is identical either way.
#[derive(Clone)]
pub struct MemoryVectorStore {
    #[allow(dead_code)]
    data_dir: String,
    model: Option<Arc<EmbeddingClient>>,
    collection: Option<VectorCollection>,
    healthy: bool,
}

impl MemoryVectorStore {
    /// `MemoryVectorStore.__init__(self, data_dir, embedding_model=None)`.
    ///
    /// Async because `_initialize` may build the embedding client (an HTTP
    /// dimension probe) when none is injected. NEVER raises: an init failure
    /// leaves the instance `unhealthy` (the Python catches and logs).
    pub async fn new(data_dir: &str, embedding_model: Option<Arc<EmbeddingClient>>) -> Self {
        let mut store = MemoryVectorStore {
            data_dir: data_dir.to_string(),
            model: embedding_model,
            collection: None,
            healthy: false,
        };
        store.initialize().await;
        store
    }

    /// `_initialize(self)`.
    ///
    /// If no model was injected, build one via `get_embedding_client()`
    /// (`raise RuntimeError` -> here: log + stay unhealthy when the factory
    /// returns `None`). Open the `"odysseus_memories"` collection and mark
    /// healthy. The whole body is the Python's `try`/`except` — any failure just
    /// leaves `_healthy = False`.
    async fn initialize(&mut self) {
        // if self._model is None: self._model = get_embedding_client()
        if self.model.is_none() {
            match get_embedding_client().await {
                Some(m) => {
                    let m = Arc::new(m);
                    logger::info(&format!("MemoryVectorStore using embeddings: {}", m.url()));
                    self.model = Some(m);
                }
                None => {
                    // raise RuntimeError("No embedding backend available") -> caught.
                    logger::error("MemoryVectorStore init failed: No embedding backend available");
                    return;
                }
            }
        }

        // client.get_or_create_collection(name=..., metadata={hnsw:space:cosine})
        match VectorCollection::get_or_create(COLLECTION_NAME) {
            Ok(c) => {
                let count = c.count().unwrap_or(0);
                self.collection = Some(c);
                self.healthy = true;
                logger::info(&format!("MemoryVectorStore ready (entries={count})"));
            }
            Err(e) => {
                logger::error(&format!("MemoryVectorStore init failed: {e}"));
            }
        }
    }

    /// `healthy` property — `self._healthy`.
    pub fn healthy(&self) -> bool {
        self.healthy
    }

    /// `_embed(self, texts) -> List[List[float]]`.
    ///
    /// `vecs = self._model.encode(texts, normalize_embeddings=True)`. Errors
    /// propagate to the caller (the Python `encode` raises on a down endpoint).
    async fn embed(&self, texts: &[String]) -> PyResult<Vec<Vec<f32>>> {
        let model = self.model.as_ref().expect("embed called without an embedding model");
        model.encode(texts, true).await
    }

    /// `count(self) -> int` — number of stored vectors (`0` when unhealthy).
    pub fn count(&self) -> i64 {
        if !self.healthy {
            return 0;
        }
        self.collection.as_ref().map(|c| c.count().unwrap_or(0)).unwrap_or(0)
    }

    /// `add(self, memory_id, text)` — add a single memory entry to the index.
    ///
    /// Skips when the id already exists (the Python `if existing["ids"]: return`).
    /// A no-op when unhealthy. Errors propagate (the Python lets `encode`/`add`
    /// raise here — only `remove` is wrapped in a `try`).
    pub async fn add(&self, memory_id: &str, text: &str) -> PyResult<()> {
        if !self.healthy {
            return Ok(());
        }
        let collection = self.collection.as_ref().unwrap();
        // existing = collection.get(ids=[memory_id]); if existing["ids"]: return
        let existing = collection.get_by_ids(&[memory_id.to_string()])?;
        if !existing.ids.is_empty() {
            return Ok(());
        }
        let embeddings = self.embed(&[text.to_string()]).await?;
        collection.add(
            &[memory_id.to_string()],
            &embeddings,
            &[text.to_string()],
            &[memory_meta()],
        )?;
        Ok(())
    }

    /// `remove(self, memory_id)` — remove a memory entry (O(1)).
    ///
    /// A no-op when unhealthy. Errors are swallowed with a warning (the Python
    /// `try/except` around the delete).
    pub fn remove(&self, memory_id: &str) {
        if !self.healthy {
            return;
        }
        let collection = self.collection.as_ref().unwrap();
        if let Err(e) = collection.delete(&[memory_id.to_string()]) {
            logger::warning(&format!("memory remove {memory_id}: {e}"));
        }
    }

    /// `search(self, query, k=8) -> List[dict]`.
    ///
    /// Search for the most relevant memory ids by semantic similarity. Returns
    /// `[{"memory_id", "score"}]` where `score = round(1 - distance, 4)`. Empty
    /// when unhealthy or the collection is empty. Errors propagate (the Python
    /// does not wrap this in a `try`).
    pub async fn search(&self, query: &str, k: i64) -> PyResult<Vec<MemoryHit>> {
        if !self.healthy {
            return Ok(Vec::new());
        }
        let collection = self.collection.as_ref().unwrap();
        let count = collection.count()?;
        if count == 0 {
            return Ok(Vec::new());
        }

        let embeddings = self.embed(&[query.to_string()]).await?;
        let query_vec: &[f32] = embeddings.first().map(|v| v.as_slice()).unwrap_or(&[]);
        // actual_k = min(k, count)
        let actual_k = k.min(count);
        let results = collection.query(query_vec, actual_k, None)?;

        let mut out: Vec<MemoryHit> = Vec::new();
        let ids = &results.ids[0];
        // for idx, mid in enumerate(results["ids"][0]):
        for (idx, mid) in ids.iter().enumerate() {
            let distance = results.distances[0][idx];
            out.push(MemoryHit {
                memory_id: mid.clone(),
                score: round4(1.0 - distance),
            });
        }
        Ok(out)
    }

    /// `find_similar(self, text, threshold=0.92) -> Optional[str]`.
    ///
    /// Returns the nearest memory id when its similarity (`1 - distance`) is at
    /// least `threshold`, else `None`. `None` when unhealthy or empty. Errors
    /// propagate (no `try` in the Python).
    pub async fn find_similar(&self, text: &str, threshold: f64) -> PyResult<Option<String>> {
        if !self.healthy {
            return Ok(None);
        }
        let collection = self.collection.as_ref().unwrap();
        if collection.count()? == 0 {
            return Ok(None);
        }

        let embeddings = self.embed(&[text.to_string()]).await?;
        let query_vec: &[f32] = embeddings.first().map(|v| v.as_slice()).unwrap_or(&[]);
        let results = collection.query(query_vec, 1, None)?;

        // if results["ids"][0]: ...
        let ids = &results.ids[0];
        if !ids.is_empty() {
            let distance = results.distances[0][0];
            let similarity = 1.0 - distance;
            if similarity >= threshold {
                return Ok(Some(ids[0].clone()));
            }
        }
        Ok(None)
    }

    /// `rebuild(self, memories)`.
    ///
    /// Rebuild the entire index from a list of memory entries (each a JSON object
    /// with `id` and `text` keys). Drops and recreates the collection, then adds
    /// the entries in batches of 100. A no-op when unhealthy. Errors propagate
    /// (no `try` around the body in the Python).
    pub async fn rebuild(&mut self, memories: &[Value]) -> PyResult<()> {
        if !self.healthy {
            return Ok(());
        }

        // Delete and recreate the collection for a clean rebuild.
        let _ = vector_store::delete_collection(COLLECTION_NAME); // try/except: pass
        let collection = VectorCollection::get_or_create(COLLECTION_NAME)?;

        // Collect (id, text) for entries with both a non-empty stripped text and id.
        let mut texts: Vec<String> = Vec::new();
        let mut ids: Vec<String> = Vec::new();
        for mem in memories {
            // text = mem.get("text", "").strip(); mid = mem.get("id", "")
            let text = mem.get("text").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
            let mid = mem.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();
            if !text.is_empty() && !mid.is_empty() {
                texts.push(text);
                ids.push(mid);
            }
        }

        if !texts.is_empty() {
            // Batch in chunks of 100.
            let mut i = 0;
            while i < texts.len() {
                let end = (i + 100).min(texts.len());
                let batch_texts = &texts[i..end];
                let batch_ids = &ids[i..end];
                let embeddings = self.embed(batch_texts).await?;
                let metas: Vec<Value> = (0..batch_ids.len()).map(|_| memory_meta()).collect();
                collection.add(batch_ids, &embeddings, batch_texts, &metas)?;
                i = end;
            }
        }

        self.collection = Some(collection);
        logger::info(&format!("MemoryVectorStore rebuilt with {} entries", ids.len()));
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round4_half_away() {
        assert!((round4(0.123449) - 0.1234).abs() < 1e-12);
        assert!((round4(0.99995) - 1.0).abs() < 1e-12);
    }

    #[test]
    fn memory_meta_shape() {
        assert_eq!(memory_meta(), json!({"source": "memory"}));
    }

    #[test]
    fn memory_hit_to_value() {
        let h = MemoryHit { memory_id: "m1".into(), score: 0.9876 };
        assert_eq!(h.to_value(), json!({"memory_id": "m1", "score": 0.9876}));
    }

    #[test]
    fn unhealthy_store_is_inert() {
        // A store with no collection/model behaves like the Python's
        // `_healthy == False` path: count() == 0 and the mutating ops no-op.
        let store = MemoryVectorStore {
            data_dir: "data".into(),
            model: None,
            collection: None,
            healthy: false,
        };
        assert_eq!(store.count(), 0);
        assert!(!store.healthy());
        store.remove("anything"); // no panic, no-op
    }
}
