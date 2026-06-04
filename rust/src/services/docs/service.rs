// services/docs/service.rs  <- services/docs/service.py
//! Docs service — personal document RAG.
//!
//! ## Port classification — PORT_VIA_DB (`all(web, db)`)
//!
//! A thin facade over the already-ported async [`RAGManager`]
//! (`crate::src::rag_manager`), which itself wraps `VectorRAG` (web embeddings
//! + the rusqlite vector store). This module ports the Python `DocChunk`,
//! `IndexResult`, and `DocsService` faithfully.
//!
//! ### ORPHAN facade
//!
//! `DocsService` has **no in-tree caller**: `app.py` wires personal-document RAG
//! through `RAGManager` directly (and the live entrypoint is the disabled
//! `rag_singleton`). This facade is ported for `services/` surface parity only.
//!
//! ### `&mut self` impedance (`rebuild_index`)
//!
//! `RAGManager::rebuild_index` takes `&mut self` (a clean rebuild mutates the
//! collection handle / `healthy`). The Python `DocsService` held a plain `self.rag`
//! attribute and called every method on it, including the mutating `rebuild_index`,
//! without ceremony (Python has no borrow checker). To preserve that — letting the
//! sync `rebuild_index(&self)` and the `async` read methods coexist on one
//! `DocsService` — the wrapped `RAGManager` is stored behind a [`Mutex`]. The async
//! methods lock briefly to delegate; `rebuild_index` locks to take the `&mut`.
//!
//! ### Vibecode drift — `index()` stat keys
//!
//! The Python `index()` reads `result.get("indexed", 0)` / `result.get("failed", 0)`,
//! but `RAGManager.index_personal_documents` returns a dict keyed
//! `indexed_count` / `failed_count` (see `VectorRAG.index_personal_documents`). So
//! in Python those `.get` calls fall through to their `0` defaults and `IndexResult`
//! is effectively always `indexed=0, failed=0`. This port reproduces that exactly
//! (it looks up `"indexed"` / `"failed"` / `"errors"`, which are absent), preserving
//! the observable — if broken — behaviour rather than silently "fixing" the keys.

use serde_json::Value;
use tokio::sync::Mutex;

use crate::src::rag_manager::RAGManager;

/// A retrieved document chunk.
///
/// Mirrors the Python `@dataclass DocChunk`. `metadata` defaults to `None`
/// (the Python field default), hence `Option<Value>` with a `Default` impl.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct DocChunk {
    /// The chunk text.
    pub text: String,
    /// The source document.
    pub source: String,
    /// The relevance score.
    pub score: f64,
    /// Optional metadata dict (Python field default `None`).
    pub metadata: Option<Value>,
}

/// Result of indexing documents.
///
/// Mirrors the Python `@dataclass IndexResult`.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct IndexResult {
    /// Number of documents indexed.
    pub indexed: i64,
    /// Number of documents that failed.
    pub failed: i64,
    /// Per-document error strings.
    pub errors: Vec<String>,
}

/// Document RAG service.
///
/// Mirrors the Python `DocsService`. Usage (Python docstring):
/// ```text
/// service = DocsService()
/// await service.index("/path/to/docs")
/// results = await service.query("what is async await?")
/// ```
pub struct DocsService {
    /// The wrapped `RAGManager` (`self.rag`).
    ///
    /// Behind a `Mutex` so the sync `rebuild_index(&self)` can borrow it
    /// `&mut` (see the `&mut self` impedance note in the module docs).
    rag: Mutex<RAGManager>,
}

impl DocsService {
    /// `DocsService.__init__(self, persist_dir="data/chroma")`.
    ///
    /// Builds the wrapped `RAGManager`. Async because `RAGManager::new` runs an
    /// HTTP embedding-dimension probe. The Python default `persist_dir` is
    /// `"data/chroma"`; this port keeps the same default via `new_default`.
    pub async fn new(persist_dir: &str) -> Self {
        let rag = RAGManager::new(persist_dir).await;
        DocsService {
            rag: Mutex::new(rag),
        }
    }

    /// `DocsService()` — the Python default-arg constructor (`persist_dir="data/chroma"`).
    pub async fn new_default() -> Self {
        Self::new("data/chroma").await
    }

    /// `async def query(self, query, top_k=5) -> List[DocChunk]`.
    ///
    /// Searches the document index and maps each result dict to a `DocChunk`
    /// via the Python nested-`.get` fallback chain:
    ///   * `text  = r.get("text", r.get("content", ""))`
    ///   * `source = r.get("source", r.get("metadata", {}).get("source", "unknown"))`
    ///   * `score = r.get("score", 0.0)`
    ///   * `metadata = r.get("metadata")`
    ///
    /// Ported verbatim. (The canonical `RAGManager.search` dicts are keyed
    /// `document`/`metadata`/`similarity`, so most of these `.get`s fall through to
    /// their defaults — preserved faithfully, matching the vibecoded Python.)
    ///
    /// Non-object (non-dict) elements are filtered out before mapping, mirroring the
    /// Python `if isinstance(r, dict)` guard on the list comprehension.
    pub async fn query(&self, query: &str, top_k: i64) -> Vec<DocChunk> {
        let results = self.rag.lock().await.search(query, top_k).await;
        results
            .iter()
            .filter(|r| r.is_object())
            .map(|r| DocChunk {
                text: get_str(r, "text")
                    .or_else(|| get_str(r, "content"))
                    .unwrap_or_default(),
                source: get_str(r, "source")
                    .or_else(|| {
                        // r.get("metadata", {}).get("source", ...)
                        r.get("metadata")
                            .and_then(|m| m.get("source"))
                            .and_then(|s| s.as_str())
                            .map(|s| s.to_string())
                    })
                    .unwrap_or_else(|| "unknown".to_string()),
                score: r.get("score").and_then(Value::as_f64).unwrap_or(0.0),
                metadata: r.get("metadata").cloned(),
            })
            .collect()
    }

    /// `query(query)` with the Python default `top_k=5`.
    pub async fn query_default(&self, query: &str) -> Vec<DocChunk> {
        self.query(query, 5).await
    }

    /// `async def index(self, directory) -> IndexResult`.
    ///
    /// Indexes documents from `directory`. See the module-level "Vibecode drift"
    /// note: the Python keys (`indexed`/`failed`/`errors`) do not match the keys
    /// the underlying method produces (`indexed_count`/`failed_count`), so these
    /// lookups fall through to their defaults — ported verbatim.
    pub async fn index(&self, directory: &str) -> IndexResult {
        let result = self
            .rag
            .lock()
            .await
            .index_personal_documents(directory)
            .await;
        IndexResult {
            indexed: result.get("indexed").and_then(Value::as_i64).unwrap_or(0),
            failed: result.get("failed").and_then(Value::as_i64).unwrap_or(0),
            errors: result
                .get("errors")
                .and_then(Value::as_array)
                .map(|arr| {
                    arr.iter()
                        .map(|e| e.as_str().map(|s| s.to_string()).unwrap_or_else(|| e.to_string()))
                        .collect()
                })
                .unwrap_or_default(),
        }
    }

    /// `async def add_document(self, text, metadata) -> bool` — add one document.
    pub async fn add_document(&self, text: &str, metadata: &Value) -> bool {
        self.rag.lock().await.add_document(text, metadata).await
    }

    /// `def get_stats(self) -> Dict[str, Any]` — index statistics.
    ///
    /// Sync on the Python side; `RAGManager::get_stats` is sync. Locks the mutex
    /// to reach the wrapped manager.
    pub async fn get_stats(&self) -> Value {
        self.rag.lock().await.get_stats()
    }

    /// `def rebuild_index(self) -> bool` — rebuild the entire index.
    ///
    /// `RAGManager::rebuild_index` needs `&mut self`; the `Mutex` guard yields a
    /// `&mut RAGManager`, so this delegates cleanly (see the `&mut self`
    /// impedance note in the module docs).
    pub async fn rebuild_index(&self) -> bool {
        self.rag.lock().await.rebuild_index()
    }
}

/// `r.get(key)` as a `String` when the value is a JSON string, else `None`.
///
/// Helper for the `query` nested-`.get` fallback chain.
fn get_str(value: &Value, key: &str) -> Option<String> {
    value.get(key).and_then(Value::as_str).map(|s| s.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// Helper: apply the same filter+map chain that `query()` uses, but over a
    /// caller-supplied `Vec<Value>`. This lets us unit-test the logic without a
    /// live `RAGManager` / HTTP backend.
    fn map_results(results: &[Value]) -> Vec<DocChunk> {
        results
            .iter()
            .filter(|r| r.is_object())
            .map(|r| DocChunk {
                text: get_str(r, "text")
                    .or_else(|| get_str(r, "content"))
                    .unwrap_or_default(),
                source: get_str(r, "source")
                    .or_else(|| {
                        r.get("metadata")
                            .and_then(|m| m.get("source"))
                            .and_then(|s| s.as_str())
                            .map(|s| s.to_string())
                    })
                    .unwrap_or_else(|| "unknown".to_string()),
                score: r.get("score").and_then(Value::as_f64).unwrap_or(0.0),
                metadata: r.get("metadata").cloned(),
            })
            .collect()
    }

    /// Non-dict (non-object) values in the result list must be silently dropped,
    /// mirroring the Python `if isinstance(r, dict)` guard.
    #[test]
    fn query_filter_non_object_results() {
        let results = vec![
            json!({"text": "hello", "source": "doc.txt", "score": 0.9}),
            json!("a plain string"),   // must be filtered out
            json!(42),                  // must be filtered out
            json!(null),                // must be filtered out
            json!([1, 2, 3]),           // array — not an object, must be filtered out
            json!({"text": "world", "source": "other.txt", "score": 0.5}),
        ];

        let chunks = map_results(&results);

        // Only the two object entries should survive.
        assert_eq!(chunks.len(), 2);
        assert_eq!(chunks[0].text, "hello");
        assert_eq!(chunks[0].source, "doc.txt");
        assert!((chunks[0].score - 0.9).abs() < 1e-9);

        assert_eq!(chunks[1].text, "world");
        assert_eq!(chunks[1].source, "other.txt");
        assert!((chunks[1].score - 0.5).abs() < 1e-9);
    }

    /// When the result list is entirely non-object values the output must be empty.
    #[test]
    fn query_filter_all_non_object_returns_empty() {
        let results = vec![json!("string"), json!(1), json!(true), json!(null)];
        let chunks = map_results(&results);
        assert!(chunks.is_empty());
    }

    /// The nested fallback chain for `text` (`r.get("content", "")`) and
    /// `source` (`r.get("metadata", {}).get("source", "unknown")`) still works
    /// after the filter is in place.
    #[test]
    fn query_fallback_chain_still_works() {
        let results = vec![json!({
            "content": "fallback text",
            "metadata": {"source": "via_metadata.txt"},
            "score": 0.7,
        })];

        let chunks = map_results(&results);
        assert_eq!(chunks.len(), 1);
        assert_eq!(chunks[0].text, "fallback text");
        assert_eq!(chunks[0].source, "via_metadata.txt");
        assert!((chunks[0].score - 0.7).abs() < 1e-9);
        assert!(chunks[0].metadata.is_some());
    }

    /// `get_str` returns `None` for missing or non-string fields.
    #[test]
    fn get_str_returns_none_for_missing_and_non_string() {
        let v = json!({"key": 123, "str_key": "hello"});
        assert_eq!(get_str(&v, "missing"), None);
        assert_eq!(get_str(&v, "key"), None); // number, not string
        assert_eq!(get_str(&v, "str_key"), Some("hello".to_string()));
    }
}
