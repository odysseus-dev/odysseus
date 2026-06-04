// src/rag_vector.rs  <- src/rag_vector.py
//! Vector-based RAG using the SQLite-backed `vectors` table + API embeddings.
//!
//! Features: persistent storage, hybrid search (vector + keyword), sentence-aware
//! chunking, configurable embedding endpoint via `EMBEDDING_URL` env var.
//!
//! ## Port classification — PORT_VIA_DB (web)
//!
//! The Python `VectorRAG` drives a ChromaDB *collection* object. Per the project
//! infra decision (the deliberate ChromaDB->SQLite swap, same spirit as the
//! SQLAlchemy->rusqlite choice), this port builds on `crate::src::vector_store`
//! — the `VectorCollection` facade over the shared `vectors` table — and on
//! `crate::src::embeddings::EmbeddingClient` (the HTTP embedding client). The
//! collection name is `"odysseus_rag"`.
//!
//! Everything else is a faithful port: the hybrid `0.7 * vector + 0.3 * keyword`
//! score (rounded to 4 dp), the keyword-overlap fallback, the sentence-boundary
//! chunker with its exact CHAR-based overlap arithmetic, `os.walk`-style directory
//! indexing (via `walkdir`), and the metadata-filtered delete/reindex helpers.
//!
//! ### Documented deviations
//!
//! * **doc_id stable hash.** Python computes `doc_id = f"doc_{hash(text) %
//!   10**16}"`. CPython's `hash()` is `PYTHONHASHSEED`-randomized, so there is no
//!   cross-process value to preserve — only intra-run dedup consistency. The Rust
//!   port uses a FIXED hasher (`std::collections::hash_map::DefaultHasher`, whose
//!   `::new()` keys are constant `0,0` — NOT randomized), so the same text maps to
//!   the same id within and across runs. The `% 10**16` (a non-negative modulus
//!   in Python) becomes `u64 % 10_000_000_000_000_000`, also in `[0, 10^16)`.
//! * **`round(x, 4)`** uses Python banker's rounding; the Rust port rounds
//!   half-away-from-zero. Cosmetic only — it affects displayed `distance` /
//!   `similarity` digits, not which documents rank.
//! * **PDF text extraction.** Python's `index_personal_documents` imports
//!   `src.personal_docs.extract_pdf_text` (a `pypdf` wrapper returning `""` on
//!   any error) for the `.pdf` branch. The canonical port of that free function
//!   lives in `personal_docs.rs` (a sibling module that lands after this one in
//!   the translation order); to keep this module self-contained and the build
//!   clean, the `.pdf` branch extracts text via the `pdf-extract` crate inline
//!   with the IDENTICAL `""`-on-error semantics. When `personal_docs.rs` is
//!   wired in, both paths share the same observable behaviour.
//! * **`healthy` semantics.** Python `_initialize_system` builds the embedding
//!   client + ChromaDB collection eagerly in `__init__`; `healthy` is
//!   `self._healthy and self._collection is not None`. The Rust constructor is
//!   `async` (the embedding-client factory does an HTTP dimension probe), so the
//!   eager init is reproduced in `VectorRAG::new(...).await` / the injected-client
//!   constructor; `healthy()` mirrors the Python predicate.
//!
//! Feature gate: `web` (HTTP embeddings + the rusqlite-backed vector store).

use std::sync::Arc;

use serde_json::{json, Map, Value};

use crate::error::PyResult;
use crate::pylog as logger;
use crate::src::embeddings::{get_embedding_client, EmbeddingClient};
use crate::src::vector_store::{self, VectorCollection};

/// `DEFAULT_FILE_EXTENSIONS` — the extensions `index_personal_documents` indexes
/// (a `set`; only membership is tested, so a slice is the faithful analogue).
pub const DEFAULT_FILE_EXTENSIONS: &[&str] = &[
    ".txt", ".md", ".py", ".json", ".yaml", ".yml", ".csv", ".html", ".css", ".js", ".pdf",
];

/// `VECTOR_WEIGHT = 0.7`.
const VECTOR_WEIGHT: f64 = 0.7;
/// `KEYWORD_WEIGHT = 0.3`.
const KEYWORD_WEIGHT: f64 = 0.3;

/// `COLLECTION_NAME = "odysseus_rag"`.
pub const COLLECTION_NAME: &str = "odysseus_rag";

/// `round(x, 4)` — half-away-from-zero (documented banker's-rounding drift).
fn round4(x: f64) -> f64 {
    (x * 10_000.0).round() / 10_000.0
}

/// `_generate_doc_id(text, owner="") -> "doc_{hash(key)}"` with a FIXED
/// (non-randomized) hasher.
///
/// The id is owner-scoped: two owners can index byte-identical chunks without the
/// second one's add early-returning on the first's id and being silently dropped
/// from their owner-filtered search results. The key is `f"{owner}\x00{text}"`
/// when `owner` is non-empty, else the bare `text` — so the unowned/base index
/// keeps its existing (text-only) ids and isn't re-churned.
///
/// See the module docstring: CPython `hash()` is `PYTHONHASHSEED`-randomized, so
/// there is no value to preserve cross-process; we use a fixed hasher for stable
/// intra/inter-run dedup. `% 10**16` keeps the same `[0, 10^16)` range as Python's
/// non-negative modulus.
fn doc_id_for(text: &str, owner: &str) -> String {
    use std::collections::hash_map::DefaultHasher;
    use std::hash::{Hash, Hasher};
    // key = f"{owner}\x00{text}" if owner else text
    let key = if owner.is_empty() {
        text.to_string()
    } else {
        format!("{owner}\u{0}{text}")
    };
    let mut h = DefaultHasher::new();
    key.hash(&mut h);
    let v = h.finish() % 10_000_000_000_000_000_u64;
    format!("doc_{v}")
}

/// `metadata.get("owner") or ""` — the owner string for id-scoping. A missing,
/// `null`, non-string, or empty `owner` all reproduce the legacy text-only id.
fn owner_of(metadata: &Value) -> String {
    metadata
        .get("owner")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

/// One hybrid-search result row (the Python `dict` each candidate becomes).
///
/// `distance` / `similarity` / `vector_similarity` / `keyword_score` are stored
/// already rounded (matching Python's `round(..., 4)` at construction). For the
/// keyword fallback, `distance` is `0` and `similarity` is the raw integer
/// overlap count (Python stores an `int` there); `search_type` distinguishes the
/// two shapes.
#[derive(Debug, Clone)]
pub struct SearchHit {
    pub id: String,
    pub document: String,
    pub metadata: Value,
    pub distance: f64,
    pub similarity: f64,
    pub vector_similarity: Option<f64>,
    pub keyword_score: Option<f64>,
    /// `"keyword_fallback"` for the fallback path; `None` for the hybrid path.
    pub search_type: Option<String>,
}

impl SearchHit {
    /// Render this hit as the Python `dict` (a `serde_json::Value` object) so
    /// callers that expect the JSON shape (`r["document"]`, `r["metadata"]`, …)
    /// port 1:1.
    pub fn to_value(&self) -> Value {
        let mut m = Map::new();
        m.insert("id".into(), json!(self.id));
        m.insert("document".into(), json!(self.document));
        m.insert("metadata".into(), self.metadata.clone());
        m.insert("distance".into(), json!(self.distance));
        m.insert("similarity".into(), json!(self.similarity));
        if let Some(v) = self.vector_similarity {
            m.insert("vector_similarity".into(), json!(v));
        }
        if let Some(k) = self.keyword_score {
            m.insert("keyword_score".into(), json!(k));
        }
        if let Some(s) = &self.search_type {
            m.insert("search_type".into(), json!(s));
        }
        Value::Object(m)
    }
}

/// RAG system using the SQLite vector store with hybrid search.
///
/// Mirrors the Python `VectorRAG`. `collection`/`model` are `Option` so the
/// `healthy` predicate (`self._healthy and self._collection is not None`) ports
/// faithfully; the embedding client is shared as `Arc<EmbeddingClient>` (matching
/// `app_initializer` sharing one instance across rag/memory/personal-docs).
pub struct VectorRAG {
    persist_directory: String,
    collection: Option<VectorCollection>,
    model: Option<Arc<EmbeddingClient>>,
    healthy: bool,
}

impl VectorRAG {
    /// `VectorRAG.__init__(self, persist_directory="data/chroma")`.
    ///
    /// Async because `_initialize_system` builds the embedding client (an HTTP
    /// dimension probe). Creates `persist_directory` (mkdir parents=True,
    /// exist_ok=True) then runs `_initialize_system`. NEVER raises: an init
    /// failure leaves the instance `unhealthy` (Python catches and sets
    /// `_healthy = False`).
    pub async fn new(persist_directory: &str) -> Self {
        let mut rag = VectorRAG {
            persist_directory: persist_directory.to_string(),
            collection: None,
            model: None,
            healthy: false,
        };
        let _ = std::fs::create_dir_all(persist_directory);
        rag.initialize_system(None).await;
        rag
    }

    /// `VectorRAG.__init__` with an injected, pre-built embedding client.
    ///
    /// Mirrors `app_initializer` constructing one `EmbeddingClient` and sharing it
    /// across the vector stores instead of letting each call
    /// `get_embedding_client()`. The Python `_initialize_system` only calls the
    /// factory when no model is set; here the caller supplies it.
    pub async fn with_embedding_client(persist_directory: &str, model: Arc<EmbeddingClient>) -> Self {
        let mut rag = VectorRAG {
            persist_directory: persist_directory.to_string(),
            collection: None,
            model: None,
            healthy: false,
        };
        let _ = std::fs::create_dir_all(persist_directory);
        rag.initialize_system(Some(model)).await;
        rag
    }

    /// `_initialize_system(self) -> bool`.
    ///
    /// Builds (or accepts) the embedding client and opens the collection. On any
    /// failure logs an error and leaves the instance unhealthy (the Python's
    /// `except Exception` branch).
    async fn initialize_system(&mut self, injected_model: Option<Arc<EmbeddingClient>>) -> bool {
        // self._model = get_embedding_client(); if None: raise RuntimeError(...)
        let model = match injected_model {
            Some(m) => m,
            None => match get_embedding_client().await {
                Some(m) => Arc::new(m),
                None => {
                    logger::error("VectorRAG init failed: No embedding backend available");
                    self.healthy = false;
                    return false;
                }
            },
        };
        logger::info(&format!("Embedding: {} model={}", model.url(), model.model()));

        // client.get_or_create_collection(name=COLLECTION_NAME, metadata={hnsw:space:cosine})
        let collection = match VectorCollection::get_or_create(COLLECTION_NAME) {
            Ok(c) => c,
            Err(e) => {
                logger::error(&format!("VectorRAG init failed: {e}"));
                self.healthy = false;
                return false;
            }
        };

        let count = collection.count().unwrap_or(0);
        logger::info(&format!("VectorRAG ready ({count} docs)"));

        self.model = Some(model);
        self.collection = Some(collection);
        self.healthy = true;
        true
    }

    /// `_embed(self, texts) -> List[List[float]]`.
    ///
    /// `vecs = self._model.encode(texts, normalize_embeddings=True)`. Returns the
    /// `Vec<Vec<f32>>` shape the vector store consumes. Errors propagate (the
    /// Python `encode` raises on a down endpoint, caught by the callers' `try`).
    async fn embed(&self, texts: &[String]) -> PyResult<Vec<Vec<f32>>> {
        let model = self
            .model
            .as_ref()
            .expect("embed called on unhealthy VectorRAG");
        model.encode(texts, true).await
    }

    // -- Properties ---------------------------------------------------------

    /// `healthy` property — `self._healthy and self._collection is not None`.
    pub fn healthy(&self) -> bool {
        self.healthy && self.collection.is_some()
    }

    /// `collection` property — the underlying `VectorCollection` (exposed for
    /// direct access by personal-doc routes, matching the Python property).
    pub fn collection(&self) -> Option<&VectorCollection> {
        self.collection.as_ref()
    }

    /// `persist_directory` (read-only mirror of the Python attribute).
    pub fn persist_directory(&self) -> &str {
        &self.persist_directory
    }

    // -- Document operations ------------------------------------------------

    /// `add_document(self, text, metadata) -> bool`.
    ///
    /// Returns `true` if added (or already present), `false` on a validation
    /// failure or error. `metadata` must be a non-empty object.
    pub async fn add_document(&self, text: &str, metadata: &Value) -> bool {
        if !self.healthy() {
            logger::error("Collection not initialized");
            return false;
        }
        // if not text or not isinstance(text, str): return False
        if text.is_empty() {
            return false;
        }
        // if not metadata or not isinstance(metadata, dict): return False
        let is_nonempty_obj = matches!(metadata, Value::Object(m) if !m.is_empty());
        if !is_nonempty_obj {
            return false;
        }
        let collection = self.collection.as_ref().unwrap();
        match self.add_document_inner(collection, text, metadata).await {
            Ok(b) => b,
            Err(e) => {
                logger::error(&format!("add_document failed: {e}"));
                false
            }
        }
    }

    async fn add_document_inner(
        &self,
        collection: &VectorCollection,
        text: &str,
        metadata: &Value,
    ) -> PyResult<bool> {
        // doc_id = _generate_doc_id(text, metadata.get("owner") or "")
        let doc_id = doc_id_for(text, &owner_of(metadata));
        // existing = collection.get(ids=[doc_id]); if existing["ids"]: return True
        let existing = collection.get_by_ids(std::slice::from_ref(&doc_id))?;
        if !existing.ids.is_empty() {
            return Ok(true); // already exists
        }
        let embeddings = self.embed(&[text.to_string()]).await?;
        collection.add(
            &[doc_id],
            &embeddings,
            &[text.to_string()],
            std::slice::from_ref(metadata),
        )?;
        Ok(true)
    }

    /// `add_documents_batch(self, docs) -> dict`.
    ///
    /// `docs` is a list of `(text, metadata)` pairs. Filters out invalid pairs
    /// (empty text / empty-or-non-object metadata), dedups by `doc_id`, then adds
    /// the survivors in batches of 100. Returns the Python result dict.
    pub async fn add_documents_batch(&self, docs: &[(String, Value)]) -> Value {
        if !self.healthy() {
            return result_err("Collection not initialized");
        }
        if docs.is_empty() {
            return result_err("Empty document list");
        }

        // valid = [(t, m) for t, m in docs if t and isinstance(t,str) and m and isinstance(m,dict)]
        let valid: Vec<&(String, Value)> = docs
            .iter()
            .filter(|(t, m)| !t.is_empty() && matches!(m, Value::Object(o) if !o.is_empty()))
            .collect();
        if valid.is_empty() {
            return result_err("No valid documents");
        }

        let collection = self.collection.as_ref().unwrap();
        match self
            .add_documents_batch_inner(collection, docs, &valid)
            .await
        {
            Ok(v) => v,
            Err(e) => result_err(&e.to_string()),
        }
    }

    async fn add_documents_batch_inner(
        &self,
        collection: &VectorCollection,
        docs: &[(String, Value)],
        valid: &[&(String, Value)],
    ) -> PyResult<Value> {
        // Dedup against existing ids.
        let mut new_texts: Vec<String> = Vec::new();
        let mut new_metas: Vec<Value> = Vec::new();
        let mut new_ids: Vec<String> = Vec::new();
        for (t, m) in valid {
            // doc_id = _generate_doc_id(t, m.get("owner") or "")
            let doc_id = doc_id_for(t, &owner_of(m));
            let existing = collection.get_by_ids(std::slice::from_ref(&doc_id))?;
            if existing.ids.is_empty() {
                new_texts.push(t.clone());
                new_metas.push(m.clone());
                new_ids.push(doc_id);
            }
        }

        if !new_texts.is_empty() {
            // Batch in chunks of 100.
            let mut i = 0;
            while i < new_texts.len() {
                let end = (i + 100).min(new_texts.len());
                let batch_texts = &new_texts[i..end];
                let batch_ids = &new_ids[i..end];
                let batch_metas = &new_metas[i..end];
                let embeddings = self.embed(batch_texts).await?;
                collection.add(batch_ids, &embeddings, batch_texts, batch_metas)?;
                i = end;
            }
        }

        let mut m = Map::new();
        m.insert("success".into(), json!(true));
        m.insert("added_count".into(), json!(new_texts.len()));
        m.insert("total_count".into(), json!(docs.len()));
        m.insert("failed_count".into(), json!(docs.len() - valid.len()));
        Ok(Value::Object(m))
    }

    // -- Search — hybrid: vector similarity + keyword overlap ---------------

    /// `search(self, query, k=5, owner=None) -> List[dict]`.
    ///
    /// Hybrid search: vector similarity (`1 - distance`) blended `0.7/0.3` with
    /// the query-word overlap fraction. On any error, falls back to the
    /// keyword-only scan. Returns the top `k` hits sorted by descending hybrid
    /// score (reverse-stable, matching Python's `list.sort` stability).
    pub async fn search(&self, query: &str, k: i64, owner: Option<&str>) -> Vec<SearchHit> {
        if !self.healthy() {
            return Vec::new();
        }
        if query.is_empty() {
            return Vec::new();
        }
        let collection = self.collection.as_ref().unwrap();
        let total = collection.count().unwrap_or(0);
        if total == 0 {
            return Vec::new();
        }

        match self.search_inner(collection, query, k, owner, total).await {
            Ok(hits) => hits,
            Err(e) => {
                logger::error(&format!("search failed: {e}"));
                self.keyword_search_fallback(query, k, owner)
            }
        }
    }

    // mirrors Python rag_vector.py:202 `for idx in range(len(results["ids"][0]))`
    // indexing across the parallel ids/distances/documents/metadatas arrays.
    #[allow(clippy::needless_range_loop)]
    async fn search_inner(
        &self,
        collection: &VectorCollection,
        query: &str,
        k: i64,
        owner: Option<&str>,
        total: i64,
    ) -> PyResult<Vec<SearchHit>> {
        // fetch_k = min(k*3, max(k, 20), count)
        let mut fetch_k = (k * 3).min(k.max(20)).min(total);
        if owner.is_some() {
            // fetch_k = min(fetch_k * 2, count)
            fetch_k = (fetch_k * 2).min(total);
        }

        let query_embeddings = self.embed(&[query.to_string()]).await?;
        let query_vec: &[f32] = query_embeddings.first().map(|v| v.as_slice()).unwrap_or(&[]);

        // where_filter = {"owner": owner} if owner else None
        let where_filter = owner.map(|o| json!({ "owner": o }));

        let results = collection.query(query_vec, fetch_k, where_filter.as_ref())?;

        // query_words = set(query.lower().split())
        let query_words: std::collections::HashSet<String> =
            query.to_lowercase().split_whitespace().map(|s| s.to_string()).collect();

        let mut candidates: Vec<SearchHit> = Vec::new();
        let ids = &results.ids[0];
        for idx in 0..ids.len() {
            let doc_id = ids[idx].clone();
            let distance = results.distances[0][idx];
            let doc_text = results.documents[0][idx].clone();
            let meta = results.metadatas[0][idx].clone();

            // vector_sim = 1.0 - distance
            let vector_sim = 1.0 - distance;

            // keyword overlap fraction over the query words.
            let doc_words: std::collections::HashSet<String> =
                doc_text.to_lowercase().split_whitespace().map(|s| s.to_string()).collect();
            let overlap = query_words.intersection(&doc_words).count();
            let keyword_score = if query_words.is_empty() {
                0.0
            } else {
                overlap as f64 / query_words.len() as f64
            };

            let hybrid_score = (VECTOR_WEIGHT * vector_sim) + (KEYWORD_WEIGHT * keyword_score);

            candidates.push(SearchHit {
                id: doc_id,
                document: doc_text,
                metadata: meta,
                distance: round4(distance),
                similarity: round4(hybrid_score),
                vector_similarity: Some(round4(vector_sim)),
                keyword_score: Some(round4(keyword_score)),
                search_type: None,
            });
        }

        // candidates.sort(key=lambda c: c["similarity"], reverse=True) — stable.
        candidates.sort_by(|a, b| b.similarity.total_cmp(&a.similarity));
        let take = (k.max(0) as usize).min(candidates.len());
        let top: Vec<SearchHit> = candidates.into_iter().take(take).collect();
        let preview: String = query.chars().take(60).collect();
        logger::info(&format!("Hybrid search for '{preview}': {} results", top.len()));
        Ok(top)
    }

    /// `_keyword_search_fallback(self, query, k=5, owner=None) -> List[dict]`.
    ///
    /// Fetch every document and score by how many query words appear as a
    /// substring of the lower-cased document. Keeps only positive scores, sorts
    /// descending (stable), returns the top `k`. `distance` is `0`, `similarity`
    /// is the integer overlap count, `search_type = "keyword_fallback"`.
    fn keyword_search_fallback(&self, query: &str, k: i64, owner: Option<&str>) -> Vec<SearchHit> {
        let collection = match self.collection.as_ref() {
            Some(c) => c,
            None => return Vec::new(),
        };
        let result = (|| -> PyResult<Vec<SearchHit>> {
            if collection.count()? == 0 {
                return Ok(Vec::new());
            }
            // all_docs = collection.get(include=["documents", "metadatas"])
            let all_docs = collection.get_where(None)?;
            if all_docs.ids.is_empty() {
                return Ok(Vec::new());
            }

            // query_words = query.lower().split()  (a list; duplicates count)
            let query_words: Vec<String> =
                query.to_lowercase().split_whitespace().map(|s| s.to_string()).collect();

            let mut scored: Vec<SearchHit> = Vec::new();
            for i in 0..all_docs.documents.len() {
                let doc = &all_docs.documents[i];
                let meta = &all_docs.metadatas[i];
                if let Some(o) = owner {
                    // Match the primary path's strict where={"owner": owner}
                    // filter: skip any chunk whose meta.owner != owner. A
                    // missing/null/empty/non-string owner is NOT equal to a
                    // requested owner string, so those owner-less docs are now
                    // dropped (the old `if doc_owner and doc_owner != owner` let
                    // them leak into another user's results).
                    //   if meta.get("owner") != owner: continue
                    if meta.get("owner").and_then(|v| v.as_str()) != Some(o) {
                        continue;
                    }
                }
                let doc_lower = doc.to_lowercase();
                // score = sum(1 for w in query_words if w in doc_lower)
                let score = query_words.iter().filter(|w| doc_lower.contains(w.as_str())).count();
                if score > 0 {
                    scored.push(SearchHit {
                        id: all_docs.ids[i].clone(),
                        document: doc.clone(),
                        metadata: meta.clone(),
                        distance: 0.0,
                        similarity: score as f64,
                        vector_similarity: None,
                        keyword_score: None,
                        search_type: Some("keyword_fallback".to_string()),
                    });
                }
            }

            // scored.sort(key=lambda x: x["similarity"], reverse=True) — stable.
            scored.sort_by(|a, b| b.similarity.total_cmp(&a.similarity));
            let take = (k.max(0) as usize).min(scored.len());
            Ok(scored.into_iter().take(take).collect())
        })();
        match result {
            Ok(v) => v,
            Err(e) => {
                logger::error(&format!("keyword fallback failed: {e}"));
                Vec::new()
            }
        }
    }

    // -- Index management ---------------------------------------------------

    /// `rebuild_index(self) -> bool`.
    ///
    /// Drop and recreate the collection (a clean rebuild). Sets `healthy = true`
    /// on success, `false` on error.
    pub fn rebuild_index(&mut self) -> bool {
        // client.delete_collection(COLLECTION_NAME) — swallow errors.
        let _ = vector_store::delete_collection(COLLECTION_NAME);
        match VectorCollection::get_or_create(COLLECTION_NAME) {
            Ok(c) => {
                self.collection = Some(c);
                self.healthy = true;
                true
            }
            Err(e) => {
                logger::error(&format!("rebuild_index failed: {e}"));
                self.healthy = false;
                false
            }
        }
    }

    /// `get_stats(self) -> dict`.
    pub fn get_stats(&self) -> Value {
        if !self.healthy() {
            return json!({ "error": "Collection not initialized" });
        }
        let collection = self.collection.as_ref().unwrap();
        match collection.count() {
            Ok(count) => {
                let model_str = match &self.model {
                    Some(m) => format!("{} @ {}", m.model(), m.url()),
                    None => "N/A".to_string(),
                };
                json!({
                    "document_count": count,
                    "embedding_model": model_str,
                    "persist_directory": self.persist_directory,
                    "collection_name": COLLECTION_NAME,
                    "healthy": true,
                })
            }
            Err(e) => {
                logger::error(&format!("get_stats failed: {e}"));
                json!({ "error": e.to_string(), "healthy": false })
            }
        }
    }

    // -- Directory indexing -------------------------------------------------

    /// `index_personal_documents(self, directory, file_extensions=None,
    /// owner=None) -> dict`.
    ///
    /// `os.walk(directory)` (via `walkdir`), filtered to `file_extensions`
    /// (default `DEFAULT_FILE_EXTENSIONS`); `.pdf` files use the `pdf-extract`
    /// inline analogue of `extract_pdf_text` (see the module docstring), others
    /// are read as UTF-8 text. Each non-empty file is split into chunks and
    /// added with `chunk_id` metadata. Returns the Python result dict.
    pub async fn index_personal_documents(
        &self,
        directory: &str,
        file_extensions: Option<&[&str]>,
        owner: Option<&str>,
    ) -> Value {
        let exts: &[&str] = file_extensions.unwrap_or(DEFAULT_FILE_EXTENSIONS);

        let mut indexed: i64 = 0;
        let mut failed: i64 = 0;

        // for root, _, files in os.walk(directory): for fname in files: ...
        // walkdir yields files in OS directory order (the same arbitrary order
        // os.walk uses — neither is sorted), so this is a faithful traversal.
        let walker = walkdir::WalkDir::new(directory).into_iter();
        for entry in walker.filter_map(|e| e.ok()) {
            if !entry.file_type().is_file() {
                continue;
            }
            let path = entry.path();
            let fname = match path.file_name().and_then(|n| n.to_str()) {
                Some(n) => n.to_string(),
                None => continue,
            };
            // ext = Path(fname).suffix.lower()
            let ext = suffix_lower(&fname);
            if !exts.contains(&ext.as_str()) {
                continue;
            }
            let fpath = path.to_string_lossy().into_owned();
            // root = the file's parent directory (os.walk's `root`).
            let root = path
                .parent()
                .map(|p| p.to_string_lossy().into_owned())
                .unwrap_or_default();

            // try: content = extract_pdf_text(fpath) | open(...).read()
            // except Exception: failed += 1
            let content = if ext == ".pdf" {
                // extract_pdf_text returns "" on any error (never raises), so the
                // `.pdf` branch never counts as a per-file failure on its own —
                // it just yields no chunks (matching the Python).
                extract_pdf_text(&fpath)
            } else {
                // open(fpath, 'r', encoding='utf-8').read() — a non-UTF-8/unreadable
                // file raises in Python (UnicodeDecodeError/OSError -> caught ->
                // failed += 1).
                match std::fs::read_to_string(&fpath) {
                    Ok(s) => s,
                    Err(e) => {
                        logger::error(&format!("index {fpath}: {e}"));
                        failed += 1;
                        continue;
                    }
                }
            };
            // if not content or not content.strip(): continue
            if content.trim().is_empty() {
                continue;
            }
            let mut meta = Map::new();
            meta.insert("source".into(), json!(fpath));
            meta.insert("filename".into(), json!(fname));
            meta.insert("directory".into(), json!(root));
            meta.insert("type".into(), json!(ext));
            if let Some(o) = owner {
                meta.insert("owner".into(), json!(o));
            }

            for (i, chunk) in self.split_into_chunks(&content, 1000, 200).into_iter().enumerate() {
                let mut chunk_meta = meta.clone();
                chunk_meta.insert("chunk_id".into(), json!(i));
                if self.add_document(&chunk, &Value::Object(chunk_meta)).await {
                    indexed += 1;
                } else {
                    failed += 1;
                }
            }
        }

        let mut m = Map::new();
        m.insert("success".into(), json!(true));
        m.insert("indexed_count".into(), json!(indexed));
        m.insert("failed_count".into(), json!(failed));
        m.insert("message".into(), json!(format!("Indexed {indexed} chunks from {directory}")));
        Value::Object(m)
    }

    /// `remove_directory(self, directory) -> dict`.
    ///
    /// Remove all chunks under `directory` (recursively), and nothing else.
    ///
    /// Selection is a Rust-side path-boundary match on each chunk's stored
    /// `source` full path, NOT a metadata `where` filter. A plain substring/prefix
    /// would over-delete siblings — removing `/docs` must not touch `/docs2` or
    /// `/docs_personal`. We therefore match `source == directory` or `source`
    /// startswith `directory + os.sep`, the same boundary rule directory exclusion
    /// uses. `directory` is abspath-normalized so it matches the absolute `source`
    /// that indexing always stores, regardless of how the caller passed it in.
    pub fn remove_directory(&self, directory: &str) -> Value {
        if !self.healthy() {
            return result_err("Collection not initialized");
        }
        // directory = os.path.abspath(directory)
        let directory = abspath(directory);
        let collection = self.collection.as_ref().unwrap();
        let result = (|| -> PyResult<Value> {
            // results = collection.get(include=["metadatas"]) — fetch everything,
            // then scan by path boundary on each chunk's stored `source`.
            let results = collection.get_where(None)?;
            // ids = [results["ids"][i] for i, m in enumerate(results["metadatas"])
            //        if isinstance(m, dict) and isinstance(m.get("source"), str)
            //        and (m["source"] == directory
            //             or m["source"].startswith(directory + os.sep))]
            let sep = std::path::MAIN_SEPARATOR;
            let prefix = format!("{directory}{sep}");
            let mut ids: Vec<String> = Vec::new();
            for (i, m) in results.metadatas.iter().enumerate() {
                let source = match m {
                    Value::Object(_) => m.get("source").and_then(|s| s.as_str()),
                    _ => None,
                };
                if let Some(src) = source {
                    if src == directory || src.starts_with(&prefix) {
                        ids.push(results.ids[i].clone());
                    }
                }
            }
            if ids.is_empty() {
                return Ok(json!({
                    "success": true, "removed_count": 0, "message": "No docs found"
                }));
            }
            collection.delete(&ids)?;
            let n = ids.len();
            logger::info(&format!("Removed {n} chunks from {directory}"));
            Ok(json!({
                "success": true,
                "removed_count": n,
                "message": format!("Removed {n} chunks"),
            }))
        })();
        match result {
            Ok(v) => v,
            Err(e) => {
                logger::error(&format!("remove_directory {directory}: {e}"));
                result_err(&e.to_string())
            }
        }
    }

    /// `reindex_directory(self, directory, file_extensions=None) -> dict`.
    pub async fn reindex_directory(
        &self,
        directory: &str,
        file_extensions: Option<&[&str]>,
    ) -> Value {
        let remove_result = self.remove_directory(directory);
        // if not remove_result.get("success"): return remove_result
        if !remove_result.get("success").and_then(|v| v.as_bool()).unwrap_or(false) {
            return remove_result;
        }
        let index_result = self.index_personal_documents(directory, file_extensions, None).await;
        let removed_count = remove_result.get("removed_count").cloned().unwrap_or(json!(0));
        let index_msg = index_result
            .get("message")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let removed_n = removed_count.as_i64().unwrap_or(0);
        json!({
            "success": index_result.get("success").and_then(|v| v.as_bool()).unwrap_or(false),
            "message": format!(
                "Re-index for {directory}: removed {removed_n}, {index_msg}"
            ),
            "removed_count": removed_count,
            "indexed_count": index_result.get("indexed_count").cloned().unwrap_or(json!(0)),
            "failed_count": index_result.get("failed_count").cloned().unwrap_or(json!(0)),
        })
    }

    // -- Sentence-boundary-aware chunking -----------------------------------

    /// `_split_into_chunks(self, text, chunk_size=1000, overlap=200) ->
    /// List[str]`.
    ///
    /// Faithful CHAR-based port. Splits on sentence boundaries (`(?<=[.!?])\s+`)
    /// or blank-line breaks (`\n{2,}`), then greedily packs sentences into chunks
    /// of at most `chunk_size` characters, carrying the trailing sentences as
    /// overlap. The `regex` crate has no lookbehind, so the `(?<=[.!?])\s+`
    /// boundary is reproduced by a manual scan that splits on whitespace runs
    /// preceded by `.`/`!`/`?` (and blank-line runs), keeping the punctuation with
    /// the preceding sentence — exactly the substrings Python's `re.split`
    /// produces.
    pub fn split_into_chunks(&self, text: &str, chunk_size: usize, overlap: usize) -> Vec<String> {
        if text.is_empty() {
            return Vec::new();
        }
        // if len(text) <= chunk_size: return [text]  (len = CHARACTER count)
        let char_len = text.chars().count();
        if char_len <= chunk_size {
            return vec![text.to_string()];
        }

        // sentences = re.split(r'(?<=[.!?])\s+|\n{2,}', text)
        // then strip + drop empties.
        let raw = split_sentences(text);
        let sentences: Vec<String> = raw
            .into_iter()
            .map(|s| s.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect();

        let mut chunks: Vec<String> = Vec::new();
        let mut current_chunk: Vec<String> = Vec::new();
        let mut current_len: usize = 0;

        for sentence in &sentences {
            let sent_len = sentence.chars().count();

            // A single sentence longer than chunk_size: flush, then hard-split.
            if sent_len > chunk_size {
                if !current_chunk.is_empty() {
                    chunks.push(current_chunk.join(" "));
                    current_chunk.clear();
                    current_len = 0;
                }
                // for start in range(0, sent_len, chunk_size - overlap):
                //     chunks.append(sentence[start:start + chunk_size])
                let step = chunk_size - overlap;
                let sent_chars: Vec<char> = sentence.chars().collect();
                let mut start = 0;
                while start < sent_len {
                    let end = (start + chunk_size).min(sent_len);
                    chunks.push(sent_chars[start..end].iter().collect());
                    start += step;
                }
                continue;
            }

            // if current_len + sent_len + 1 > chunk_size and current_chunk:
            if current_len + sent_len + 1 > chunk_size && !current_chunk.is_empty() {
                chunks.push(current_chunk.join(" "));
                // Keep the last few sentences for overlap.
                let mut overlap_sentences: Vec<String> = Vec::new();
                let mut overlap_len: usize = 0;
                for s in current_chunk.iter().rev() {
                    let sl = s.chars().count();
                    if overlap_len + sl > overlap {
                        break;
                    }
                    overlap_sentences.insert(0, s.clone());
                    overlap_len += sl + 1;
                }
                current_chunk = overlap_sentences;
                // current_len = sum(len(s) for s in current_chunk)
                //               + max(0, len(current_chunk) - 1)
                let sum_len: usize = current_chunk.iter().map(|s| s.chars().count()).sum();
                current_len = sum_len + current_chunk.len().saturating_sub(1);
            }

            current_chunk.push(sentence.clone());
            // current_len += sent_len + (1 if current_len > 0 else 0)
            current_len += sent_len + if current_len > 0 { 1 } else { 0 };
        }

        if !current_chunk.is_empty() {
            chunks.push(current_chunk.join(" "));
        }

        if chunks.is_empty() {
            vec![text.to_string()]
        } else {
            chunks
        }
    }

    // -- Delete by metadata -------------------------------------------------

    /// `delete_by_source(self, source) -> int`.
    ///
    /// Remove all chunks whose `metadata["source"]` equals `source`. Returns the
    /// number removed (`0` on error or when nothing matches).
    pub fn delete_by_source(&self, source: &str) -> i64 {
        if !self.healthy() {
            return 0;
        }
        let collection = self.collection.as_ref().unwrap();
        let result = (|| -> PyResult<i64> {
            let where_filter = json!({ "source": source });
            let results = collection.get_where(Some(&where_filter))?;
            if results.ids.is_empty() {
                return Ok(0);
            }
            collection.delete(&results.ids)?;
            logger::info(&format!("Deleted {} chunks for source={source}", results.ids.len()));
            Ok(results.ids.len() as i64)
        })();
        match result {
            Ok(n) => n,
            Err(e) => {
                logger::error(&format!("delete_by_source failed: {e}"));
                0
            }
        }
    }

    // -- Convenience --------------------------------------------------------

    /// `retrieve(self, query, k=5) -> List[str]` — the document texts of the top
    /// `k` hybrid-search hits.
    pub async fn retrieve(&self, query: &str, k: i64) -> Vec<String> {
        self.search(query, k, None)
            .await
            .into_iter()
            .map(|r| r.document)
            .collect()
    }
}

// ---------------------------------------------------------------------------
// Free helpers
// ---------------------------------------------------------------------------

/// `{"success": False, "message": msg}` — the Python error-result dict.
fn result_err(msg: &str) -> Value {
    json!({ "success": false, "message": msg })
}

/// `os.path.abspath(p)` — prepend the CWD and lexically normalise without
/// resolving symlinks. `std::path::absolute` is the std analogue (matching the
/// sibling `personal_docs.rs` helper). On the rare failure (e.g. empty path with
/// no CWD), fall back to the raw input so the comparison still behaves like a
/// stringly path.
fn abspath(p: &str) -> String {
    match std::path::absolute(p) {
        Ok(pb) => pb.to_string_lossy().into_owned(),
        Err(_) => p.to_string(),
    }
}

/// `Path(fname).suffix.lower()` — the lower-cased final extension (with the dot),
/// or `""` when there is no extension. Mirrors `pathlib.PurePath.suffix`: a
/// leading-dot dotfile with no other dot (`.bashrc`) has an empty suffix.
fn suffix_lower(fname: &str) -> String {
    // pathlib: suffix is the substring from the LAST '.' that is not the first
    // character of the name component, else "".
    let name = fname.rsplit('/').next().unwrap_or(fname);
    match name.rfind('.') {
        Some(idx) if idx > 0 => name[idx..].to_lowercase(),
        _ => String::new(),
    }
}

/// `re.split(r'(?<=[.!?])\s+|\n{2,}', text)` — split into sentence-ish pieces.
///
/// The `regex` crate has no lookbehind, so this reproduces the two alternatives
/// directly, honoring `re.split`'s left-to-right, per-position alternation
/// (branch A is tried before branch B at every position):
///   * `(?<=[.!?])\s+` — whitespace whose immediately-preceding char is `.`, `!`
///     or `?`. `\s` is any whitespace, so this also fires when the whitespace run
///     *begins* with a single newline right after terminal punctuation. The
///     greedy `\s+` consumes the maximal whitespace run (spaces, tabs, newlines
///     alike) as one separator; the punctuation stays with the left piece.
///   * `\n{2,}` — a run of two-or-more newlines, regardless of the preceding
///     char. Consumes only that maximal newline run.
/// Anything else (a *single* `\n` not preceded by terminal punctuation, or other
/// whitespace not preceded by terminal punctuation) stays inside the current
/// piece, exactly as Python's regex leaves it. The returned pieces are the raw
/// substrings (the caller strips them).
fn split_sentences(text: &str) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    let n = chars.len();
    let mut pieces: Vec<String> = Vec::new();
    let mut cur = String::new();
    let mut i = 0;
    while i < n {
        let c = chars[i];
        if c.is_whitespace() {
            // Branch A (tried first, like the regex's left alternative):
            // `(?<=[.!?])\s+` — whitespace preceded by terminal punctuation.
            // `\s` includes '\n', so a single leading newline after '.', '!' or
            // '?' is a boundary too. Greedily consume the maximal whitespace run.
            let prev_is_term = cur
                .chars()
                .last()
                .map(|p| p == '.' || p == '!' || p == '?')
                .unwrap_or(false);
            if prev_is_term {
                let mut j = i;
                while j < n && chars[j].is_whitespace() {
                    j += 1;
                }
                pieces.push(std::mem::take(&mut cur));
                i = j;
                continue;
            }
            // Branch B: `\n{2,}` — a run of 2+ newlines starting here is a
            // separator (consumes only the newline run, not surrounding ws).
            if c == '\n' {
                let mut j = i;
                while j < n && chars[j] == '\n' {
                    j += 1;
                }
                if j - i >= 2 {
                    pieces.push(std::mem::take(&mut cur));
                    i = j;
                    continue;
                }
            }
            // Neither branch matched at this position: this whitespace char is
            // ordinary text. Emit just this char and advance by one so a later
            // `\n{2,}` run embedded in the same whitespace stretch is still
            // examined at its own start position (matching re's per-position
            // scan).
            cur.push(c);
            i += 1;
            continue;
        }
        cur.push(c);
        i += 1;
    }
    pieces.push(cur);
    pieces
}

/// Inline analogue of `personal_docs.extract_pdf_text` for the `.pdf` indexing
/// branch (see the module docstring): extract text via the `pdf-extract` crate,
/// returning `""` on ANY error (matching the Python `pypdf` wrapper's
/// `except ...: return ""`). The canonical free function will live in
/// `personal_docs.rs`; both share these `""`-on-error semantics.
fn extract_pdf_text(file_path: &str) -> String {
    match pdf_extract::extract_text(file_path) {
        Ok(text) => text,
        Err(e) => {
            logger::error(&format!("Failed to extract PDF text from {file_path}: {e}"));
            String::new()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round4_half_away() {
        assert!((round4(0.123449) - 0.1234).abs() < 1e-12);
        assert!((round4(0.12345) - 0.1235).abs() < 1e-12);
        assert!((round4(1.0 - 0.0) - 1.0).abs() < 1e-12);
    }

    #[test]
    fn doc_id_is_stable_and_in_range() {
        let a = doc_id_for("hello world", "");
        let b = doc_id_for("hello world", "");
        let c = doc_id_for("different text", "");
        assert_eq!(a, b); // fixed hasher -> stable within a run
        assert_ne!(a, c);
        assert!(a.starts_with("doc_"));
        let n: u64 = a.trim_start_matches("doc_").parse().unwrap();
        assert!(n < 10_000_000_000_000_000);
    }

    #[test]
    fn doc_id_is_owner_scoped() {
        // Same text, different non-empty owners -> distinct ids (so the second
        // owner's add doesn't early-return on the first owner's id).
        let alice = doc_id_for("shared chunk", "alice");
        let bob = doc_id_for("shared chunk", "bob");
        assert_ne!(alice, bob);
        // Same owner + text is stable.
        assert_eq!(alice, doc_id_for("shared chunk", "alice"));
        // Empty owner reproduces the legacy text-only id (base index unchurned).
        assert_eq!(doc_id_for("shared chunk", ""), doc_id_for("shared chunk", ""));
        assert_ne!(doc_id_for("shared chunk", ""), alice);
        // The composite key is `owner\x00text`, so an owner with no text and a
        // text with no owner do NOT collide just because the bytes concatenate
        // the same way without the NUL.
        assert_ne!(doc_id_for("xy", "a"), doc_id_for("ay", "x"));
    }

    #[test]
    fn owner_of_extracts_owner_or_empty() {
        assert_eq!(owner_of(&json!({"owner": "alice"})), "alice");
        // Missing / null / non-string / explicit-empty -> "" (legacy text id).
        assert_eq!(owner_of(&json!({})), "");
        assert_eq!(owner_of(&json!({"owner": null})), "");
        assert_eq!(owner_of(&json!({"owner": 5})), "");
        assert_eq!(owner_of(&json!({"owner": ""})), "");
    }

    #[test]
    fn abspath_is_absolute_and_idempotent() {
        let abs = abspath("some/rel/dir");
        assert!(std::path::Path::new(&abs).is_absolute());
        // Already-absolute input is preserved through std::path::absolute.
        assert_eq!(abspath(&abs), abs);
    }

    #[test]
    fn suffix_lower_matches_pathlib() {
        assert_eq!(suffix_lower("a.PDF"), ".pdf");
        assert_eq!(suffix_lower("notes.MD"), ".md");
        assert_eq!(suffix_lower("archive.tar.gz"), ".gz");
        assert_eq!(suffix_lower("README"), "");
        assert_eq!(suffix_lower(".bashrc"), ""); // leading-dot dotfile -> no suffix
        assert_eq!(suffix_lower("dir/sub/x.TxT"), ".txt");
    }

    #[test]
    fn short_text_is_single_chunk() {
        let rag = VectorRAG {
            persist_directory: String::new(),
            collection: None,
            model: None,
            healthy: false,
        };
        let out = rag.split_into_chunks("short text", 1000, 200);
        assert_eq!(out, vec!["short text".to_string()]);
    }

    #[test]
    fn empty_text_is_empty_vec() {
        let rag = VectorRAG {
            persist_directory: String::new(),
            collection: None,
            model: None,
            healthy: false,
        };
        assert!(rag.split_into_chunks("", 1000, 200).is_empty());
    }

    #[test]
    fn split_packs_sentences_with_overlap() {
        let rag = VectorRAG {
            persist_directory: String::new(),
            collection: None,
            model: None,
            healthy: false,
        };
        // 3 sentences, small chunk_size so packing + overlap kick in.
        let text = "Alpha beta gamma. Delta epsilon zeta. Eta theta iota kappa.";
        let chunks = rag.split_into_chunks(text, 30, 10);
        assert!(chunks.len() >= 2);
        // Every chunk is bounded by chunk_size in characters (sentences fit).
        for c in &chunks {
            assert!(c.chars().count() <= 40, "chunk too long: {c:?}");
        }
        // The first sentence is preserved verbatim somewhere.
        assert!(chunks.iter().any(|c| c.contains("Alpha beta gamma.")));
    }

    #[test]
    fn split_hard_splits_oversized_sentence() {
        let rag = VectorRAG {
            persist_directory: String::new(),
            collection: None,
            model: None,
            healthy: false,
        };
        // One 50-char "sentence" (no terminal punctuation/blank line) with
        // chunk_size 20, overlap 5 -> hard char-split with step 15.
        let long = "a".repeat(50);
        let chunks = rag.split_into_chunks(&long, 20, 5);
        // Hard split offsets: 0,15,30,45 -> 4 chunks (20,20,20,5).
        assert_eq!(chunks.len(), 4);
        assert_eq!(chunks[0].len(), 20);
        assert_eq!(chunks[3].len(), 5);
    }

    #[test]
    fn split_sentences_boundaries() {
        // "." + space -> boundary; single "\n" (after non-terminal char) -> no
        // boundary; "\n\n" -> boundary.
        let pieces = split_sentences("One. Two\nstill two\n\nThree?");
        let trimmed: Vec<String> = pieces.into_iter().map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect();
        assert_eq!(
            trimmed,
            vec![
                "One.".to_string(),
                "Two\nstill two".to_string(),
                "Three?".to_string(),
            ]
        );
    }

    #[test]
    fn split_sentences_single_newline_after_terminal() {
        // re.split(r'(?<=[.!?])\s+|\n{2,}', ...): because \n is whitespace, a
        // single newline immediately after .!? is a boundary (the (?<=[.!?])\s+
        // branch), not just \n{2,}. Verified against CPython re.split.
        let collect = |t: &str| -> Vec<String> {
            split_sentences(t)
                .into_iter()
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .collect()
        };
        // Single '\n' after a terminal char -> two pieces.
        assert_eq!(collect("A.\nB"), vec!["A.".to_string(), "B".to_string()]);
        assert_eq!(
            collect("First sentence.\nSecond sentence."),
            vec!["First sentence.".to_string(), "Second sentence.".to_string()]
        );
        assert_eq!(collect("Q?\nA."), vec!["Q?".to_string(), "A.".to_string()]);
        // The \s+ branch greedily swallows a whole whitespace run that begins
        // with a newline after terminal punctuation.
        assert_eq!(
            collect("End.\n  \nNext"),
            vec!["End.".to_string(), "Next".to_string()]
        );
        // A single '\n' after a NON-terminal char is still ordinary text.
        assert_eq!(
            collect("no terminal\nhere"),
            vec!["no terminal\nhere".to_string()]
        );
    }

    #[test]
    fn search_hit_to_value_shape() {
        let hit = SearchHit {
            id: "doc_1".into(),
            document: "x".into(),
            metadata: json!({"owner": "u"}),
            distance: 0.1,
            similarity: 0.9,
            vector_similarity: Some(0.8),
            keyword_score: Some(0.5),
            search_type: None,
        };
        let v = hit.to_value();
        assert_eq!(v["id"], json!("doc_1"));
        assert_eq!(v["similarity"], json!(0.9));
        assert_eq!(v["vector_similarity"], json!(0.8));
        assert!(v.get("search_type").is_none());

        let fb = SearchHit {
            id: "doc_2".into(),
            document: "y".into(),
            metadata: json!({}),
            distance: 0.0,
            similarity: 3.0,
            vector_similarity: None,
            keyword_score: None,
            search_type: Some("keyword_fallback".into()),
        };
        let fv = fb.to_value();
        assert_eq!(fv["search_type"], json!("keyword_fallback"));
        assert!(fv.get("vector_similarity").is_none());
    }

    #[test]
    fn result_err_shape() {
        let v = result_err("boom");
        assert_eq!(v, json!({"success": false, "message": "boom"}));
    }

    // -- DB-backed: remove_directory path-boundary selection ----------------


    /// Serialize DB-backed tests so they don't race on the process-global
    /// `DATABASE_URL` / backend-selection cache.
    use crate::core::database::DB_TEST_LOCK as DB_GUARD;

    /// Point every connection at a fresh temp DB (NEVER the live `data/app.db`)
    /// and force the SQLite vector backend, mirroring `vector_store`'s harness.
    fn with_temp_db<F: FnOnce()>(f: F) {
        let _held = DB_GUARD.lock().unwrap_or_else(|p| p.into_inner());
        std::env::remove_var("ODYSSEUS_VECTOR_BACKEND");
        std::env::remove_var("CHROMADB_HOST");
        std::env::remove_var("CHROMADB_PORT");
        vector_store::reset_backend_selection();

        let dir = std::env::temp_dir();
        let unique = format!(
            "odysseus_rag_vector_{}_{}.db",
            std::process::id(),
            uuid::Uuid::new_v4().simple()
        );
        let path = dir.join(unique);
        std::env::set_var("DATABASE_URL", format!("sqlite:///{}", path.display()));
        let _ = std::fs::remove_file(&path);
        f();
        let _ = std::fs::remove_file(&path);
        vector_store::reset_backend_selection();
    }

    /// A healthy `VectorRAG` with a real SQLite-backed collection but no embedding
    /// model — enough to exercise the metadata-scan paths (`remove_directory`,
    /// `delete_by_source`) which never call `_embed`.
    fn rag_with(collection: VectorCollection) -> VectorRAG {
        VectorRAG {
            persist_directory: String::new(),
            collection: Some(collection),
            model: None,
            healthy: true,
        }
    }

    fn add_chunk(c: &VectorCollection, id: &str, source: &str) {
        c.add(
            &[id.to_string()],
            &[vec![1.0_f32, 0.0]],
            &[format!("doc {id}")],
            &[json!({ "source": source })],
        )
        .unwrap();
    }

    #[test]
    fn remove_directory_matches_by_path_boundary_not_substring() {
        with_temp_db(|| {
            let c = VectorCollection::get_or_create(COLLECTION_NAME).unwrap();
            let sep = std::path::MAIN_SEPARATOR;
            // Absolute target dir + sibling dirs that a naive prefix/substring
            // match would wrongly sweep up.
            let target = abspath("docs");
            let inside = format!("{target}{sep}a.md");
            let inside_nested = format!("{target}{sep}sub{sep}b.md");
            let sibling_prefix = format!("{target}2{sep}c.md"); // /docs2/...
            let sibling_under = format!("{target}_personal{sep}d.md"); // /docs_personal/...
            let elsewhere = format!("{}{sep}other{sep}e.md", abspath("unrelated"));

            add_chunk(&c, "inside", &inside);
            add_chunk(&c, "inside_nested", &inside_nested);
            add_chunk(&c, "exact", &target); // source == directory
            add_chunk(&c, "sibling2", &sibling_prefix);
            add_chunk(&c, "sibling_personal", &sibling_under);
            add_chunk(&c, "elsewhere", &elsewhere);
            assert_eq!(c.count().unwrap(), 6);

            let rag = rag_with(c);
            // Pass the directory un-normalized; remove_directory abspaths it.
            let res = rag.remove_directory("docs");
            assert_eq!(res["success"], json!(true));
            // Exactly the three chunks AT or UNDER /docs: inside, inside_nested,
            // exact. /docs2 and /docs_personal must survive.
            assert_eq!(res["removed_count"], json!(3));

            let remaining = rag.collection().unwrap().get_where(None).unwrap();
            let mut left: Vec<String> = remaining.ids.clone();
            left.sort();
            assert_eq!(
                left,
                vec![
                    "elsewhere".to_string(),
                    "sibling2".to_string(),
                    "sibling_personal".to_string(),
                ]
            );
        });
    }

    #[test]
    fn remove_directory_no_match_returns_zero() {
        with_temp_db(|| {
            let c = VectorCollection::get_or_create(COLLECTION_NAME).unwrap();
            let elsewhere = format!("{}{}x.md", abspath("real"), std::path::MAIN_SEPARATOR);
            add_chunk(&c, "x", &elsewhere);
            let rag = rag_with(c);
            let res = rag.remove_directory("nonexistent_dir");
            assert_eq!(res["success"], json!(true));
            assert_eq!(res["removed_count"], json!(0));
            assert_eq!(res["message"], json!("No docs found"));
            // The unrelated chunk is untouched.
            assert_eq!(rag.collection().unwrap().count().unwrap(), 1);
        });
    }

    #[test]
    fn remove_directory_unhealthy_errors() {
        let rag = VectorRAG {
            persist_directory: String::new(),
            collection: None,
            model: None,
            healthy: false,
        };
        let res = rag.remove_directory("docs");
        assert_eq!(res, json!({"success": false, "message": "Collection not initialized"}));
    }
}
