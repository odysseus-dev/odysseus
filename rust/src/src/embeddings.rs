// src/embeddings.rs  <- src/embeddings.py
//! Embedding clients for RAG and memory vector search.
//!
//! Priority order (mirroring the Python factory):
//!   1. HTTP API (Ollama / vLLM / llama.cpp) — set `EMBEDDING_URL` in `.env`
//!   2. Local fastembed (ONNX) — zero-config fallback
//!
//! ## Components
//!
//! * `EmbeddingClient` (the HTTP API client) — over `reqwest` (the
//!   `httpx.Client` analogue): batch of 64, OpenAI `{"data":[{"embedding",
//!   "index"}]}` parsing (sort by `index`), hand-rolled L2 normalisation, and
//!   the lazy dimension probe. The Python `httpx.Timeout(connect=3, read=10,
//!   write=5, pool=3)` becomes a reqwest builder with a 3s connect timeout and
//!   a 10s overall request timeout (the closest faithful analogue).
//! * `FastEmbedClient` (local ONNX) — a REAL client over the `fastembed` crate
//!   (fastembed-rs: the same ONNX models + `TextEmbedding` type as the Python
//!   `fastembed` lib). It is ALWAYS compiled (the crate has no feature flags),
//!   so the `get_embedding_client` factory can fall back to it exactly as
//!   Python's `try HTTP -> except -> FastEmbedClient()` does.
//! * `_load_persisted_endpoint` / `reset_http_embed_state` / the
//!   `_http_embed_down` process latch / `get_embedding_client`. The factory's
//!   HTTP-down fall-back constructs the REAL local `FastEmbedClient` (mirroring
//!   Python's `try HTTP -> except -> FastEmbedClient()`), falling through to
//!   `None` only if even the local backend fails to initialise — exactly like
//!   Python's *both-backends-failed* path.
//!
//! ### Backend / path deviations (documented)
//!
//! * The Python `EmbeddingClient.encode` returns a `numpy` `(N, dim)` float32
//!   array; consumers immediately call `.tolist()` to get a list-of-lists. The
//!   faithful Rust analogue is `Vec<Vec<f32>>` (the `.tolist()` shape), so
//!   `rag_vector` / `memory_vector` indexing ports 1:1.
//! * `_load_persisted_endpoint` reads `<repo>/data/embedding_endpoint.json`.
//!   Python computes this from `os.path.dirname(os.path.dirname(
//!   os.path.abspath(__file__)))` — the repository root — and so it does **not**
//!   honour `ODYSSEUS_DATA_DIR`. We replicate the source-relative path via
//!   `crate::core::constants::BASE_DIR` (the same repo-root anchor), NOT
//!   `DATA_DIR`. Same for the FastEmbed cache path note.
//! * `get_embedding_client` writes the resolved endpoint back into the process
//!   env (`EMBEDDING_URL` / `EMBEDDING_MODEL`) via `pyos::set_var`, exactly as
//!   `os.environ["EMBEDDING_URL"] = url` does, so other code that re-reads
//!   `os.getenv` sees the persisted custom endpoint.
//! * `FastEmbedClient` maps the Python model *string*
//!   to a `fastembed::EmbeddingModel` enum variant (the crate is enum-driven,
//!   not string-driven): the Python default `sentence-transformers/all-MiniLM-
//!   L6-v2` -> `AllMiniLML6V2` (384-dim) and `BAAI/bge-small-en-v1.5` /
//!   `bge-small-en-v1.5` -> `BGESmallENV15`; `FASTEMBED_MODEL` is honoured and an
//!   unknown string maps to the default with a logged warning (the Python lib
//!   would itself raise on an unknown HuggingFace id — we degrade to the working
//!   default instead). DOCUMENTED DRIFT: fastembed-rs downloads its own ONNX
//!   source (`AllMiniLML6V2` -> `Qdrant/all-MiniLM-L6-v2-onnx`) into the HF-style
//!   cache; the exact on-disk `models--*` snapshot dir differs from the Python
//!   `fastembed` lib's source, so the admin panel's `_is_downloaded()` HF-layout
//!   probe sees a (correct, same-dim) but differently-named cache entry. The
//!   cache root is identical (`FASTEMBED_CACHE_PATH` env or
//!   `<repo>/data/fastembed_cache`, source-relative via `BASE_DIR`, NOT
//!   `ODYSSEUS_DATA_DIR`), matching the Python `__file__`-anchored path.

use crate::core::constants::BASE_DIR;
use crate::error::{PyError, PyResult};
use crate::pylog as logger;
use crate::pyos as os;
use once_cell::sync::Lazy;
use serde_json::Value;
use std::sync::Mutex;

/// `_DEFAULT_MODEL = "all-minilm:l6-v2"`.
const DEFAULT_MODEL: &str = "all-minilm:l6-v2";
/// `_DEFAULT_FASTEMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"`.
const DEFAULT_FASTEMBED_MODEL: &str = "sentence-transformers/all-MiniLM-L6-v2";

/// Error message for the defensive `probe_local` guard. `probe_local` is only
/// ever called by the factory on a freshly-built `Fast` variant; the `Http` arm
/// is unreachable in practice, so this message exists only to keep that arm
/// honest rather than silently faking a dimension.
const FASTEMBED_UNAVAILABLE: &str =
    "local FastEmbed embeddings: not available in the Rust port yet";

/// A drop-in embedding backend.
///
/// Python has two concrete classes (`EmbeddingClient`, `FastEmbedClient`) that
/// share a duck-typed surface (`.url`, `.model`, `.encode`,
/// `.get_sentence_embedding_dimension`). The Rust port models the sum type
/// explicitly as an enum so the factory can return either variant behind a
/// single shared `Arc<EmbeddingClient>` (as `app_initializer` shares one
/// instance across `rag_vector` / `memory_vector` / `personal_docs`).
///
/// The `Fast` variant is a REAL local ONNX client (the `fastembed` crate),
/// always compiled. The factory returns it whenever the HTTP endpoint is down
/// and the local model initialises successfully.
pub enum EmbeddingClient {
    /// The HTTP API client (the `httpx`-backed Python `EmbeddingClient`).
    Http {
        /// `self.url` — the OpenAI-compatible `/v1/embeddings` endpoint.
        url: String,
        /// `self.model` — the embedding model id.
        model: String,
        /// `self._dim` — the embedding dimension, lazily probed and cached.
        dim: Mutex<Option<usize>>,
    },
    /// The local FastEmbed client. `Box`ed because `FastEmbedClient` wraps the
    /// (large) ONNX `TextEmbedding` model behind a `Mutex`, so an unboxed variant
    /// would make every `EmbeddingClient` (incl. the small `Http` variant) carry
    /// that footprint.
    Fast(Box<FastEmbedClient>),
}

impl EmbeddingClient {
    /// `EmbeddingClient.__init__(url=None, model=None)`.
    ///
    /// `self.url = url or os.getenv("EMBEDDING_URL",
    /// f"http://{os.getenv('LLM_HOST','localhost')}:11434/v1/embeddings")`;
    /// `self.model = model or os.getenv("EMBEDDING_MODEL", _DEFAULT_MODEL)`.
    pub fn new(url: Option<String>, model: Option<String>) -> Self {
        let url = url.filter(|s| !s.is_empty()).unwrap_or_else(|| {
            os::getenv_opt("EMBEDDING_URL").filter(|s| !s.is_empty()).unwrap_or_else(|| {
                let host = os::getenv("LLM_HOST", "localhost");
                format!("http://{host}:11434/v1/embeddings")
            })
        });
        let model = model.filter(|s| !s.is_empty()).unwrap_or_else(|| {
            os::getenv("EMBEDDING_MODEL", DEFAULT_MODEL)
        });
        EmbeddingClient::Http {
            url,
            model,
            dim: Mutex::new(None),
        }
    }

    /// `self.url` — the endpoint URL (or `"local://fastembed"` for the local
    /// FastEmbed client).
    ///
    /// Public accessor so `rag_vector` / `memory_vector` can log
    /// `self._model.url` verbatim.
    pub fn url(&self) -> &str {
        match self {
            EmbeddingClient::Http { url, .. } => url,
            EmbeddingClient::Fast(f) => &f.url,
        }
    }

    /// `self.model` — the model id.
    ///
    /// Public accessor so `rag_vector` can log `self._model.model`.
    pub fn model(&self) -> &str {
        match self {
            EmbeddingClient::Http { model, .. } => model,
            EmbeddingClient::Fast(f) => &f.model,
        }
    }

    /// `get_sentence_embedding_dimension(self) -> int`.
    ///
    /// Probe the endpoint for embedding dimension if not yet known. Returns the
    /// cached value when already probed; otherwise encodes the single word
    /// `"hello"` and records `vec.shape[1]`.
    ///
    /// Async because the underlying `encode` is an HTTP call.
    pub async fn get_sentence_embedding_dimension(&self) -> PyResult<usize> {
        match self {
            EmbeddingClient::Http { model, dim, .. } => {
                // if self._dim is not None: return self._dim
                if let Some(d) = *dim.lock().unwrap() {
                    return Ok(d);
                }
                // vec = self.encode(["hello"]); self._dim = vec.shape[1]
                let vec = self.encode(&["hello".to_string()], true).await?;
                let d = vec.first().map(|row| row.len()).unwrap_or(0);
                *dim.lock().unwrap() = Some(d);
                logger::info(&format!("Embedding dimension: {d} (model={model})"));
                Ok(d)
            }
            // Delegate to the real local client's own lazy probe (embed "hello",
            // record shape[1]).
            EmbeddingClient::Fast(f) => f.get_sentence_embedding_dimension(),
        }
    }

    /// `client.get_sentence_embedding_dimension()` health check on the LOCAL
    /// FastEmbed fall-back path, run synchronously (the local backend is
    /// CPU-bound — no network round-trip, so unlike the HTTP probe it needs no
    /// async). Mirrors the Python factory's
    /// `client.get_sentence_embedding_dimension()` line before
    /// `return client`. Returns `self` on success so the factory can return it.
    fn probe_local(self) -> PyResult<Self> {
        match &self {
            EmbeddingClient::Fast(f) => {
                f.get_sentence_embedding_dimension()?;
                Ok(self)
            }
            // The factory only ever calls this on a freshly-built `Fast`
            // variant; the HTTP variant has its own async probe above.
            EmbeddingClient::Http { .. } => Err(PyError::other(FASTEMBED_UNAVAILABLE)),
        }
    }

    /// `encode(self, texts, normalize_embeddings=True) -> np.ndarray`.
    ///
    /// Encode texts via the API. Returns an `(N, dim)` float32 matrix as a
    /// `Vec<Vec<f32>>` (the Python `.tolist()` shape the callers consume).
    pub async fn encode(
        &self,
        texts: &[String],
        normalize_embeddings: bool,
    ) -> PyResult<Vec<Vec<f32>>> {
        match self {
            EmbeddingClient::Http { url, model, dim } => {
                // if not texts: return np.array([], dtype="float32")
                if texts.is_empty() {
                    return Ok(Vec::new());
                }

                // Short connect timeout so a DOWN embedding endpoint fast-fails
                // (Python: httpx.Timeout(connect=3.0, read=10.0, write=5.0,
                // pool=3.0)). reqwest's closest analogue: a 3s connect timeout
                // plus a 10s overall request timeout.
                let client = reqwest::Client::builder()
                    .connect_timeout(std::time::Duration::from_secs(3))
                    .timeout(std::time::Duration::from_secs(10))
                    .build()
                    .map_err(|e| PyError::other(format!("embedding client: {e}")))?;

                // Batch in chunks of 64 to avoid oversized requests.
                let mut all_vecs: Vec<Vec<f32>> = Vec::new();
                for batch in texts.chunks(64) {
                    let payload = serde_json::json!({
                        "input": batch,
                        "model": model,
                    });
                    let resp = client
                        .post(url)
                        .json(&payload)
                        .send()
                        .await
                        .map_err(|e| PyError::other(format!("{e}")))?;
                    // resp.raise_for_status()
                    let resp = resp
                        .error_for_status()
                        .map_err(|e| PyError::other(format!("{e}")))?;
                    let data: Value = resp
                        .json()
                        .await
                        .map_err(|e| PyError::other(format!("{e}")))?;

                    // OpenAI format: {"data": [{"embedding": [...], "index": 0}]}
                    // embeddings.sort(key=lambda e: e.get("index", 0))
                    let mut embeddings: Vec<&Value> = match data.get("data") {
                        Some(Value::Array(arr)) => arr.iter().collect(),
                        _ => Vec::new(),
                    };
                    embeddings.sort_by_key(|e| {
                        e.get("index").and_then(|i| i.as_i64()).unwrap_or(0)
                    });
                    for emb in embeddings {
                        // for emb in embeddings: all_vecs.append(emb["embedding"])
                        // `emb["embedding"]` raises KeyError on a missing key in
                        // Python; here a missing/non-array embedding surfaces as
                        // a ValueError rather than silently fabricating zeros.
                        let arr = emb.get("embedding").and_then(|v| v.as_array()).ok_or_else(
                            || PyError::value("embedding endpoint response missing 'embedding'"),
                        )?;
                        let row: Vec<f32> =
                            arr.iter().map(|x| x.as_f64().unwrap_or(0.0) as f32).collect();
                        all_vecs.push(row);
                    }
                }

                if normalize_embeddings {
                    l2_normalize(&mut all_vecs);
                }

                // if self._dim is None and vecs.size > 0: self._dim = vecs.shape[1]
                if !all_vecs.is_empty() {
                    let mut guard = dim.lock().unwrap();
                    if guard.is_none() {
                        *guard = Some(all_vecs[0].len());
                    }
                }

                Ok(all_vecs)
            }
            // Runs the local ONNX model (synchronous, CPU-bound — no network
            // round-trip, so no `.await`).
            EmbeddingClient::Fast(f) => f.encode(texts, normalize_embeddings),
        }
    }
}

/// `np.linalg.norm(vecs, axis=1, keepdims=True)` then `vecs / norms` with the
/// `np.where(norms == 0, 1, norms)` zero-norm guard. Operates in place on a
/// list-of-rows; each row is divided by its (non-zero) L2 norm.
fn l2_normalize(vecs: &mut [Vec<f32>]) {
    // if normalize_embeddings and vecs.size > 0 — a zero-row matrix is a no-op.
    for row in vecs.iter_mut() {
        let mut norm = row.iter().map(|x| (*x as f64) * (*x as f64)).sum::<f64>().sqrt();
        // norms = np.where(norms == 0, 1, norms)
        if norm == 0.0 {
            norm = 1.0;
        }
        let norm = norm as f32;
        for x in row.iter_mut() {
            *x /= norm;
        }
    }
}

// `class FastEmbedClient` — local embedding client using fastembed (ONNX). No
// external service needed. A REAL client over the `fastembed` crate
// (fastembed-rs), always compiled.

/// REAL local FastEmbed client (over the `fastembed` crate).
pub struct FastEmbedClient {
    /// `self.model` — the resolved model id string (env / default).
    pub model: String,
    /// `self.url = "local://fastembed"`.
    pub url: String,
    /// `self._embedding = TextEmbedding(...)`. The fastembed `embed` takes
    /// `&mut self`, so it lives behind a `Mutex` (the Python instance is shared
    /// across `rag_vector` / `memory_vector` behind one `Arc`).
    embedding: Mutex<fastembed::TextEmbedding>,
    /// `self._dim: Optional[int]` — lazily probed and cached.
    dim: Mutex<Option<usize>>,
}

impl FastEmbedClient {
    /// `FastEmbedClient.__init__(model=None)`.
    ///
    /// `self.model = model or os.getenv("FASTEMBED_MODEL",
    /// _DEFAULT_FASTEMBED_MODEL)`; build `TextEmbedding(model_name=self.model,
    /// cache_dir=cache_dir)` with the persistent cache under `data/` (so the
    /// model survives reboots and lands where the admin panel's
    /// `_is_downloaded()` HF-layout check looks). Maps the Python model *string*
    /// to a `fastembed::EmbeddingModel` enum variant (the crate is enum-driven).
    pub fn new(model: Option<String>) -> PyResult<Self> {
        use fastembed::{InitOptions, TextEmbedding};

        // self.model = model or os.getenv("FASTEMBED_MODEL", _DEFAULT_FASTEMBED_MODEL)
        let model = model.filter(|s| !s.is_empty()).unwrap_or_else(|| {
            os::getenv("FASTEMBED_MODEL", DEFAULT_FASTEMBED_MODEL)
        });

        // Map the model string to the fastembed enum variant. The Python lib is
        // string-driven (HuggingFace ids); fastembed-rs is enum-driven, so we
        // map the ids the app actually uses and degrade unknowns to the default
        // (the Python lib would itself raise on an unknown id).
        let embedding_model = map_model_name(&model);

        // cache_dir = os.getenv("FASTEMBED_CACHE_PATH") or
        //   os.path.join(dirname(dirname(abspath(__file__))), "data", "fastembed_cache")
        // (source-relative via BASE_DIR, NOT ODYSSEUS_DATA_DIR — matches the
        // Python __file__ anchor and the admin panel's `_cache_dir()`).
        let cache_dir = fastembed_cache_dir();
        // os.makedirs(cache_dir, exist_ok=True)
        os::makedirs(&cache_dir, true)
            .map_err(|e| PyError::other(format!("FastEmbed cache dir: {e}")))?;

        // Windows: force HuggingFace-hub to copy files instead of symlink them.
        // On a network-share/UNC cache dir Windows can't follow HF's symlinks
        // ([WinError 1463] "symbolic link cannot be followed"), so ONNX fails to
        // load the model and semantic memory dies. huggingface_hub reads this
        // flag at import time (Python: set at module top). On the Rust side
        // the fastembed crate reads the cache dir directly and does not
        // re-symlink, but setting the env vars still guards any Python
        // subprocess or side-car that spawns and reads them.
        //
        // Windows self-heal: the HF-hub cache stores model files as symlinks
        // (snapshots/<rev>/model.onnx -> ../../blobs/<hash>). On a
        // network-share / UNC data dir, or a cache copied between machines,
        // these can become dead/broken symlinks. fastembed tries to load a
        // broken symlink and fails *without* re-downloading, leaving semantic
        // memory degraded. Detect a broken-symlink model in the cache and drop
        // the contaminated hub dir so fastembed re-fetches. Best-effort; only
        // ever removes a verifiably dead link's `models--*` ancestor.
        //
        // Python source (FastEmbedClient.__init__):
        //   if os.name == "nt":
        //       os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
        //       os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        //       try:
        //           for _onnx in glob("**/*.onnx", recursive=True):
        //               if islink(_onnx) and not exists(_onnx):
        //                   walk up to models--* ancestor; shutil.rmtree(root)
        //       except Exception as _e:
        //           logger.debug(...)
        #[cfg(windows)]
        {
            // setdefault: only set if not already present in the environment.
            if os::getenv_opt("HF_HUB_DISABLE_SYMLINKS").is_none() {
                os::set_var("HF_HUB_DISABLE_SYMLINKS", "1");
            }
            if os::getenv_opt("HF_HUB_DISABLE_SYMLINKS_WARNING").is_none() {
                os::set_var("HF_HUB_DISABLE_SYMLINKS_WARNING", "1");
            }

            // Scan cache dir for broken .onnx symlinks and clear the
            // contaminated models--* hub dirs so fastembed re-downloads.
            (|| -> () {
                let cache_path = std::path::Path::new(&cache_dir);
                let onnx_files = find_onnx_files(cache_path);
                for onnx in onnx_files {
                    // os.path.islink(_onnx) and not os.path.exists(_onnx)
                    // In Rust: read_link succeeds -> it's a symlink;
                    // .exists() returns false -> the target is missing (broken).
                    let is_symlink = std::fs::read_link(&onnx).is_ok();
                    let target_exists = onnx.exists();
                    if is_symlink && !target_exists {
                        // Walk up the path to find the models--* ancestor dir.
                        if let Some(hub_root) = find_models_hub_root(&onnx) {
                            logger::warning(&format!(
                                "Embedding cache has a broken symlink ({onnx}); clearing {hub_root} \
                                 so fastembed re-downloads real files",
                                onnx = onnx.display(),
                                hub_root = hub_root.display(),
                            ));
                            let _ = std::fs::remove_dir_all(&hub_root);
                        }
                    }
                }
            })();
        }

        // self._embedding = TextEmbedding(model_name=self.model, cache_dir=cache_dir)
        let embedding = TextEmbedding::try_new(
            InitOptions::new(embedding_model).with_cache_dir(std::path::PathBuf::from(&cache_dir)),
        )
        .map_err(|e| PyError::other(format!("{e}")))?;

        logger::info(&format!("FastEmbed loaded model={model}"));
        Ok(FastEmbedClient {
            model,
            url: "local://fastembed".to_string(),
            embedding: Mutex::new(embedding),
            dim: Mutex::new(None),
        })
    }

    /// `get_sentence_embedding_dimension(self) -> int`.
    ///
    /// Return the cached dimension, else embed the single word `"hello"` and
    /// record `vec.shape[1]`.
    pub fn get_sentence_embedding_dimension(&self) -> PyResult<usize> {
        // if self._dim is not None: return self._dim
        if let Some(d) = *self.dim.lock().unwrap() {
            return Ok(d);
        }
        // vec = self.encode(["hello"]); self._dim = vec.shape[1]
        let vec = self.encode(&["hello".to_string()], true)?;
        let d = vec.first().map(|row| row.len()).unwrap_or(0);
        *self.dim.lock().unwrap() = Some(d);
        logger::info(&format!("Embedding dimension: {d} (model={})", self.model));
        Ok(d)
    }

    /// `encode(self, texts, normalize_embeddings=True) -> np.ndarray`.
    ///
    /// Encode texts locally. Returns an `(N, dim)` float32 matrix as a
    /// `Vec<Vec<f32>>` (the Python `.tolist()` shape the callers consume).
    pub fn encode(
        &self,
        texts: &[String],
        normalize_embeddings: bool,
    ) -> PyResult<Vec<Vec<f32>>> {
        // if not texts: return np.array([], dtype="float32")
        if texts.is_empty() {
            return Ok(Vec::new());
        }

        // vecs = np.array(list(self._embedding.embed(texts)), dtype="float32")
        // fastembed-rs `embed` is `&mut self` and returns Vec<Vec<f32>> directly
        // (Embedding = Vec<f32>); the `None` batch size uses the crate default.
        let mut vecs: Vec<Vec<f32>> = self
            .embedding
            .lock()
            .unwrap()
            .embed(texts, None)
            .map_err(|e| PyError::other(format!("{e}")))?;

        // if normalize_embeddings and vecs.size > 0: divide each row by its L2 norm
        if normalize_embeddings {
            l2_normalize(&mut vecs);
        }

        // if self._dim is None and vecs.size > 0: self._dim = vecs.shape[1]
        if !vecs.is_empty() {
            let mut guard = self.dim.lock().unwrap();
            if guard.is_none() {
                *guard = Some(vecs[0].len());
            }
        }

        Ok(vecs)
    }
}

/// Map the Python `fastembed` model id *string* to a fastembed-rs
/// `EmbeddingModel` enum variant.
///
/// The Python default `sentence-transformers/all-MiniLM-L6-v2` (384-dim) maps to
/// `AllMiniLML6V2`; `BAAI/bge-small-en-v1.5` (and the bare `bge-small-en-v1.5`)
/// to `BGESmallENV15`. Any unknown id degrades to the default with a logged
/// warning (the Python lib would raise on an unknown HuggingFace id; we keep the
/// vector features working with the default instead of failing the fallback).
pub fn map_model_name(model: &str) -> fastembed::EmbeddingModel {
    use fastembed::EmbeddingModel;
    match model {
        // The Python _DEFAULT_FASTEMBED_MODEL.
        DEFAULT_FASTEMBED_MODEL | "all-MiniLM-L6-v2" => EmbeddingModel::AllMiniLML6V2,
        "BAAI/bge-small-en-v1.5" | "bge-small-en-v1.5" => EmbeddingModel::BGESmallENV15,
        other => {
            logger::warning(&format!(
                "FastEmbed: unknown model '{other}'; falling back to {DEFAULT_FASTEMBED_MODEL}"
            ));
            EmbeddingModel::AllMiniLML6V2
        }
    }
}

/// The fastembed cache directory (the Python `embedding_routes._cache_dir()`).
///
/// `os.environ.get("FASTEMBED_CACHE_PATH")` if set, else the persistent
/// `<BASE_DIR>/data/fastembed_cache` (source-relative via `BASE_DIR`, NOT
/// `ODYSSEUS_DATA_DIR` — matches the Python `__file__`-anchored path). Shared by
/// `FastEmbedClient::new` and the admin `embedding_routes` so the loader and the
/// model-catalog endpoints look in exactly the same place.
pub fn fastembed_cache_dir() -> String {
    os::getenv_opt("FASTEMBED_CACHE_PATH")
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| os::path::join(&os::path::join(&BASE_DIR, "data"), "fastembed_cache"))
}

// ── Windows-only helper functions for the broken-symlink self-heal ──────────

/// Walk `dir` recursively and collect every file whose name ends in `.onnx`.
///
/// This mirrors `glob.glob(os.path.join(cache_dir, "**", "*.onnx"),
/// recursive=True)` from the Python self-heal block.  Returns an empty `Vec`
/// on any I/O error (best-effort, matching `except Exception: pass`).
#[cfg(windows)]
fn find_onnx_files(dir: &std::path::Path) -> Vec<std::path::PathBuf> {
    let mut results = Vec::new();
    let Ok(entries) = std::fs::read_dir(dir) else {
        return results;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            // recurse — best-effort, ignore errors
            results.extend(find_onnx_files(&path));
        } else {
            // Check extension (case-insensitive on Windows, but fastembed
            // always writes lowercase ".onnx").
            if path.extension().and_then(|e| e.to_str()) == Some("onnx") {
                results.push(path);
            }
        }
    }
    results
}

/// Walk up the ancestors of `path` until we find a directory component whose
/// name starts with `"models--"`.  Returns `None` if no such ancestor exists.
///
/// Mirrors the Python:
/// ```python
/// _root = _onnx
/// while os.path.basename(_root) and not os.path.basename(_root).startswith("models--"):
///     _parent = os.path.dirname(_root)
///     if _parent == _root: break
///     _root = _parent
/// if os.path.basename(_root).startswith("models--"): ...
/// ```
#[cfg(windows)]
fn find_models_hub_root(path: &std::path::Path) -> Option<std::path::PathBuf> {
    let mut current = path.to_path_buf();
    loop {
        let name = current.file_name()?.to_string_lossy().into_owned();
        if name.starts_with("models--") {
            return Some(current);
        }
        let parent = current.parent()?.to_path_buf();
        if parent == current {
            break;
        }
        current = parent;
    }
    None
}

// ────────────────────────────────────────────────────────────────────────────

/// `_load_persisted_endpoint() -> dict`.
///
/// Load the custom embedding endpoint saved from the admin panel. Reads
/// `<repo>/data/embedding_endpoint.json` (source-relative; does NOT honour
/// `ODYSSEUS_DATA_DIR`, matching Python's `__file__`-anchored path). Returns the
/// parsed object only when it carries a non-empty `url`; otherwise `{}`. All
/// errors are swallowed (Python `except Exception: pass`).
pub fn _load_persisted_endpoint() -> Value {
    // try: ... except Exception: pass; return {}
    let endpoint_file = os::path::join(&os::path::join(&BASE_DIR, "data"), "embedding_endpoint.json");
    let result = (|| -> Option<Value> {
        if !os::path::exists(&endpoint_file) {
            return None;
        }
        let text = std::fs::read_to_string(&endpoint_file).ok()?;
        let data: Value = serde_json::from_str(&text).ok()?;
        // if data.get("url"): return data  (truthy: non-empty string)
        let has_url = data
            .get("url")
            .and_then(|u| u.as_str())
            .map(|s| !s.is_empty())
            .unwrap_or(false);
        if has_url {
            Some(data)
        } else {
            None
        }
    })();
    result.unwrap_or_else(|| Value::Object(serde_json::Map::new()))
}

/// `_http_embed_down` — process-level latch: once the HTTP embedding endpoint is
/// found down, skip re-probing it for the rest of the process (avoids paying the
/// connect timeout again on every RAG/memory/tool probe). Module-level Python
/// global -> `Lazy<Mutex<bool>>`.
static HTTP_EMBED_DOWN: Lazy<Mutex<bool>> = Lazy::new(|| Mutex::new(false));

/// `reset_http_embed_state()`.
///
/// Clear the "HTTP embedding endpoint is down" latch so the next
/// `get_embedding_client()` re-probes. Call this when the embedding endpoint
/// setting changes (e.g. the user starts Ollama and saves the endpoint) —
/// otherwise a latch tripped at startup would keep us on FastEmbed for the whole
/// process even after the endpoint comes back.
pub fn reset_http_embed_state() {
    *HTTP_EMBED_DOWN.lock().unwrap() = false;
}

/// `get_embedding_client()`.
///
/// Factory: try the HTTP API first, fall back to local fastembed. Returns
/// `None` only if BOTH the HTTP endpoint and the local backend fail (Python's
/// both-failed path).
///
/// Async because the HTTP health-check (`get_sentence_embedding_dimension`) is a
/// network round-trip.
pub async fn get_embedding_client() -> Option<EmbeddingClient> {
    // Check for a persisted custom endpoint (saved from admin panel).
    let persisted = _load_persisted_endpoint();
    if let Some(url) = persisted.get("url").and_then(|u| u.as_str()).filter(|s| !s.is_empty()) {
        let model = persisted.get("model").and_then(|m| m.as_str()).unwrap_or("");
        // Also set in env so other code sees it.
        os::set_var("EMBEDDING_URL", url);
        if !model.is_empty() {
            os::set_var("EMBEDDING_MODEL", model);
        }
    }

    // Try the HTTP embedding API — unless we already found it down this process.
    let down = *HTTP_EMBED_DOWN.lock().unwrap();
    if !down {
        let client = EmbeddingClient::new(None, None);
        match client.get_sentence_embedding_dimension().await {
            Ok(_) => {
                logger::info(&format!(
                    "Using HTTP embedding API: {} model={}",
                    client.url(),
                    client.model()
                ));
                return Some(client);
            }
            Err(e) => {
                *HTTP_EMBED_DOWN.lock().unwrap() = true;
                logger::warning(&format!(
                    "HTTP embedding API unavailable ({e}); using local FastEmbed for the rest of this process"
                ));
            }
        }
    }

    // Fall back to local fastembed.
    //   try:
    //       client = FastEmbedClient()
    //       client.get_sentence_embedding_dimension()
    //       logger.info(f"Using local FastEmbed: model={client.model}")
    //       return client
    // Construct the REAL local client (and probe its dimension as the health
    // check). If construction or the probe fails this falls through to `None`,
    // exactly like Python's all-backends-failed path.
    match FastEmbedClient::new(None) {
        Ok(client) => match EmbeddingClient::Fast(Box::new(client)).probe_local() {
            Ok(c) => {
                logger::info(&format!("Using local FastEmbed: model={}", c.model()));
                Some(c)
            }
            Err(e) => {
                logger::error(&format!("FastEmbed init failed: {e}"));
                None
            }
        },
        Err(e) => {
            // Python logs ImportError as "fastembed not installed" and other
            // exceptions as "FastEmbed init failed"; a genuine init failure
            // (e.g. the ONNX model cannot be downloaded) is the latter shape.
            logger::error(&format!("FastEmbed init failed: {e}"));
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_defaults_from_env_fallback() {
        // With no EMBEDDING_URL/EMBEDDING_MODEL/LLM_HOST set, defaults match
        // the Python: http://localhost:11434/v1/embeddings and all-minilm:l6-v2.
        // (Env may be set in CI; only assert the model default path is stable
        // when EMBEDDING_MODEL is unset.)
        std::env::remove_var("EMBEDDING_URL");
        std::env::remove_var("EMBEDDING_MODEL");
        std::env::remove_var("LLM_HOST");
        let c = EmbeddingClient::new(None, None);
        assert_eq!(c.url(), "http://localhost:11434/v1/embeddings");
        assert_eq!(c.model(), DEFAULT_MODEL);
    }

    #[test]
    fn new_explicit_overrides_win() {
        let c = EmbeddingClient::new(
            Some("http://example/embed".to_string()),
            Some("my-model".to_string()),
        );
        assert_eq!(c.url(), "http://example/embed");
        assert_eq!(c.model(), "my-model");
    }

    #[test]
    fn l2_normalize_unit_rows() {
        let mut v = vec![vec![3.0f32, 4.0], vec![0.0, 0.0]];
        l2_normalize(&mut v);
        // [3,4] -> /5 -> [0.6, 0.8]; the zero row is divided by the guarded
        // norm of 1 -> stays [0,0].
        assert!((v[0][0] - 0.6).abs() < 1e-6);
        assert!((v[0][1] - 0.8).abs() < 1e-6);
        assert_eq!(v[1], vec![0.0, 0.0]);
    }

    #[test]
    fn empty_normalize_is_noop() {
        let mut v: Vec<Vec<f32>> = Vec::new();
        l2_normalize(&mut v);
        assert!(v.is_empty());
    }

    // The model-string -> enum mapping is pure (no ONNX/network): the Python
    // default and `bge-small-en-v1.5` map to their variants, and an unknown id
    // degrades to the default.
    #[test]
    fn map_model_name_known_and_unknown() {
        use fastembed::EmbeddingModel;
        assert_eq!(map_model_name(DEFAULT_FASTEMBED_MODEL), EmbeddingModel::AllMiniLML6V2);
        assert_eq!(map_model_name("all-MiniLM-L6-v2"), EmbeddingModel::AllMiniLML6V2);
        assert_eq!(map_model_name("BAAI/bge-small-en-v1.5"), EmbeddingModel::BGESmallENV15);
        assert_eq!(map_model_name("bge-small-en-v1.5"), EmbeddingModel::BGESmallENV15);
        // Unknown -> default.
        assert_eq!(map_model_name("totally/unknown-model"), EmbeddingModel::AllMiniLML6V2);
    }

    // Adversarial-review runtime parity check (ignored: needs the ONNX model
    // download). Construct the REAL FastEmbedClient and embed two short strings;
    // confirm 2 vectors of dim 384, each L2-normalized (norm ~= 1).
    #[test]
    #[ignore]
    fn fastembed_embeds_two_strings_dim384_l2() {
        let client = FastEmbedClient::new(None).expect("construct FastEmbedClient");
        let texts = vec!["hello world".to_string(), "the quick brown fox".to_string()];
        let vecs = client.encode(&texts, true).expect("encode");
        assert_eq!(vecs.len(), 2, "expected 2 vectors");
        for row in &vecs {
            assert_eq!(row.len(), 384, "expected dim 384");
            let norm = row.iter().map(|x| (*x as f64) * (*x as f64)).sum::<f64>().sqrt();
            assert!((norm - 1.0).abs() < 1e-5, "expected L2 norm ~1, got {norm}");
        }
        // Dimension probe agrees.
        assert_eq!(client.get_sentence_embedding_dimension().expect("dim"), 384);
    }

    #[test]
    fn reset_latch_clears() {
        *HTTP_EMBED_DOWN.lock().unwrap() = true;
        reset_http_embed_state();
        assert!(!*HTTP_EMBED_DOWN.lock().unwrap());
    }

    #[test]
    fn load_persisted_endpoint_missing_is_empty_object() {
        // No data/embedding_endpoint.json by default in the test sandbox; the
        // function swallows everything and returns {}.
        let v = _load_persisted_endpoint();
        // It must be an object (possibly the real one if a dev has the file);
        // we only assert the type contract.
        assert!(v.is_object());
    }

    // ── Windows-only: self-heal helper unit tests ────────────────────────────
    // These run only on Windows (the functions are #[cfg(windows)]).  They test
    // the pure path-walking logic without touching any real symlinks or the
    // ONNX cache.

    /// `find_models_hub_root` should walk up from a deeply-nested path and
    /// return the first ancestor whose name starts with `models--`.
    #[cfg(windows)]
    #[test]
    fn find_models_hub_root_finds_ancestor() {
        use std::path::PathBuf;
        // Simulate:  cache/models--org--repo/snapshots/abc123/model.onnx
        let onnx = PathBuf::from(
            r"C:\cache\models--org--repo\snapshots\abc123\model.onnx",
        );
        let hub_root = find_models_hub_root(&onnx).expect("should find models-- ancestor");
        assert_eq!(hub_root.file_name().unwrap().to_str().unwrap(), "models--org--repo");
    }

    /// `find_models_hub_root` returns `None` when no `models--*` ancestor exists.
    #[cfg(windows)]
    #[test]
    fn find_models_hub_root_returns_none_when_absent() {
        use std::path::PathBuf;
        let path = PathBuf::from(r"C:\some\random\path\model.onnx");
        assert!(find_models_hub_root(&path).is_none());
    }

    /// `find_onnx_files` returns `.onnx` files from a temp dir tree and
    /// ignores non-.onnx files (pure filesystem, no symlinks needed).
    #[cfg(windows)]
    #[test]
    fn find_onnx_files_collects_onnx_in_tree() {
        use std::fs;
        let dir = std::env::temp_dir().join(format!(
            "odysseus_test_onnx_{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .subsec_nanos()
        ));
        let sub = dir.join("sub");
        fs::create_dir_all(&sub).unwrap();
        // Create a real .onnx file and a non-.onnx file.
        fs::write(sub.join("model.onnx"), b"fake").unwrap();
        fs::write(dir.join("tokenizer.json"), b"{}").unwrap();
        let found = find_onnx_files(&dir);
        fs::remove_dir_all(&dir).ok();
        assert_eq!(found.len(), 1);
        assert!(found[0].to_string_lossy().ends_with("model.onnx"));
    }
}
