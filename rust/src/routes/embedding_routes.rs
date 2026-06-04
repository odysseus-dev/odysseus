// routes/embedding_routes.rs  <- routes/embedding_routes.py
//! Routes for managing local fastembed embedding models and custom endpoints.
//!
//! Routes WAVE 2 module **embedding_routes** (classification PORT_NOW, feature
//! gate `web`). app.py registers this as include-router #16. Mounts each
//! handler at the absolute `/api/embeddings/...` path the Python
//! `APIRouter(prefix="/api/embeddings")` yields, on the collision-free
//! `/api/embeddings/*` prefix (so it coexists with the inline `web/mod.rs` subset
//! — the additive / non-breaking rule).
//!
//! ## Port split
//!
//! The Python module has two concerns:
//!
//! 1. **The `/models/*` quartet** (`GET /models`,
//!    `POST /models/{model_name:path}/download`,
//!    `GET /models/{model_name:path}/status`,
//!    `DELETE /models/{model_name:path}`) — REAL over the `fastembed` crate
//!    (fastembed-rs), the same ONNX catalog/downloader `src::embeddings`'s
//!    `FastEmbedClient` already loads. `list_models` enumerates
//!    `TextEmbedding::list_supported_models()`, computes `downloaded` from the
//!    HF-style cache layout, and flags the `active`/`recommended` entries;
//!    `download_model` runs `TextEmbedding::try_new(...)` (which fetches the ONNX
//!    weights into the cache) on a blocking thread; `download_status` reports the
//!    cache/in-flight state; `delete_model` rmtree's the cached model dir.
//!
//!    DOCUMENTED DRIFT vs the Python `fastembed` lib: fastembed-rs is **enum**-keyed
//!    (its `EmbeddingModel` variants), not string-keyed by HuggingFace ids, and its
//!    `ModelInfo` exposes no `size_in_GB`. So the catalog `"model"` field is the
//!    fastembed-rs variant name (unique + round-trips via `FromStr` for the
//!    download/delete path params), `"size_gb"` is reported as `0` (Python defaults
//!    a missing `size_in_GB` to `0`), and `"sources.hf"`/cache lookups use the
//!    variant's `model_code`. The `active` flag maps the configured
//!    `FASTEMBED_MODEL` env string through `embeddings::map_model_name` to compare
//!    by enum. An unknown `model_name` path param is a `404 "Unknown model: …"`
//!    (Python's `if model_name not in catalog`).
//!
//!    `DELETE /models/{model_name:path}` keeps the Python pre-catalog ordering: the
//!    active-model guard (`400 "Cannot delete the active embedding model"`, compared
//!    by enum) and the in-flight-download guard run first, then the catalog/cache
//!    deletion.
//!
//! 2. **The `/endpoint` trio** (`GET`/`POST`/`DELETE /endpoint`) — PORT_NOW. These
//!    read/write the embedding-endpoint JSON config file
//!    (`<repo>/data/embedding_endpoint.json`), mutate the process env
//!    (`EMBEDDING_URL`/`EMBEDDING_MODEL`), run a `reqwest` health check on
//!    `POST`, and best-effort reset the embedding/chroma singletons by calling the
//!    reset hooks that ALREADY exist (`src::embeddings::reset_http_embed_state`,
//!    `src::chroma_client::reset_client`).
//!
//! ### Admin gate (`Depends(require_admin)`)
//!
//! Python attaches `APIRouter(prefix="/api/embeddings",
//! dependencies=[Depends(require_admin)])`, so EVERY handler is admin-only. axum
//! has no router-level dependency, so the faithful equivalent is the module
//! [`require_admin`] gate called as the FIRST statement of each handler (before any
//! other work): the handlers take `State<AppState>` + `HeaderMap` +
//! `Option<Extension<CurrentUser>>` and delegate to `auth_adapter::require_admin`
//! (which wraps `core/middleware.require_admin`, raising `403 "Admin only"`). The
//! handler bodies are factored into `*_inner` fns so the gate is a thin uniform
//! prefix and the co-located tests can exercise the bodies without an `AppState`.
//!
//! ### SSRF hardening on `POST /endpoint`
//!
//! Before persisting + probing the user-supplied URL, `set_endpoint` validates it
//! with `src::url_safety::check_outbound_url(url, block_private)`, where
//! `block_private = os.getenv("EMBEDDING_BLOCK_PRIVATE_IPS", "false").lower() ==
//! "true"`. On a rejection it raises `400 "Rejected endpoint URL: {reason}"`. This
//! runs after the `400 "URL is required"` check and BEFORE the outbound health
//! probe, exactly mirroring the Python ordering.
//!
//! ### Singleton-reset fidelity (documented)
//!
//! The Python `set_endpoint`/`clear_endpoint` reset three singletons:
//!
//! ```python
//! import src.rag_singleton as _rs
//! _rs.rag_instance = None
//! _rs._last_attempt = 0
//! try: from src.embeddings import reset_http_embed_state; reset_http_embed_state()
//! except Exception: pass
//! try: from src.chroma_client import reset_client; reset_client()
//! except Exception: pass
//! ```
//!
//! * `reset_http_embed_state()` and `reset_client()` are public reset hooks that
//!   already exist in the Rust port — called here, wrapped in the same best-effort
//!   shape as the Python `try/except` (they are infallible in Rust).
//! * The `src.rag_singleton` reset (`rag_instance = None; _last_attempt = 0`)
//!   pokes two **module globals** directly. In the Rust port those are the private
//!   `RAG_INSTANCE`/`LAST_ATTEMPT` statics inside `src::rag_singleton`, and there
//!   is **no public reset hook** for them. Per the design ("reset singletons ONLY
//!   by calling reset hooks that ALREADY exist; if a reset hook is absent just
//!   skip it, do not add new cross-file hooks"), this reset is SKIPPED. It is a
//!   no-op anyway: `src::rag_singleton::get_rag_manager()` is disabled and
//!   unconditionally returns `None` (vector document RAG is unused), so the cached
//!   `RAG_INSTANCE` is never populated and nulling it has no observable effect.
//!
//! ### Health-check error-text drift (documented)
//!
//! `POST /endpoint` runs `httpx.post(url, json=..., timeout=10)` then
//! `resp.raise_for_status()`, and on **any** exception raises
//! `HTTPException(400, f"Endpoint unreachable: {e}")`. The Rust port uses `reqwest`
//! (the project-wide `httpx` analogue) with a 10s timeout and surfaces a
//! `400 "Endpoint unreachable: <e>"` on a transport error or a non-2xx status. The
//! `<e>` text differs between `httpx` and `reqwest` (different libraries), but the
//! status (`400`), the message prefix (`"Endpoint unreachable: "`), and the
//! fail-on-non-2xx behaviour are preserved exactly.


use std::collections::HashSet;
use std::path::Path;
use std::str::FromStr;
use std::sync::Mutex;

use axum::extract::{Form, State};
use axum::http::HeaderMap;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Extension, Json, Router};
use indexmap::IndexMap;
use once_cell::sync::Lazy;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::pylog as logger;
use crate::routes::{auth_adapter, AppState, CurrentUser, HttpException};

/// `_downloading: dict` — the in-flight download guard (a Python module global).
/// Keyed by the fastembed-rs model variant name (the catalog `"model"` id).
static DOWNLOADING: Lazy<Mutex<HashSet<String>>> = Lazy::new(|| Mutex::new(HashSet::new()));

/// `RECOMMENDED_MODELS` — curated tier picks. Keyed by fastembed-rs variant name
/// (the catalog id), mirroring the Python canonical-name set's intent: a tiny
/// fast default, the smallest solid model, a quantized mid, a balanced base, and
/// the highest-quality large. (Drift: the Python set used HuggingFace ids; these
/// are the equivalent fastembed-rs variants.)
const RECOMMENDED_MODELS: &[&str] = &[
    "AllMiniLML6V2",       // 384d — fast & tiny, the default
    "BGESmallENV15",       // 384d — smallest, solid quality
    "NomicEmbedTextV15Q",  // 768d — quantized, great bang/buck
    "BGEBaseENV15",        // 768d — balanced mid-range
    "SnowflakeArcticEmbedM", // 768d — strong performer
    "BGELargeENV15",       // 1024d — highest quality
];

/// `_ENDPOINT_FILE = os.path.join(BASE_DIR, "data", "embedding_endpoint.json")`.
///
/// Python imports `from core.constants import BASE_DIR`, so the config file lives
/// at `<repo>/data/embedding_endpoint.json`. This is the **same** file
/// `src::embeddings::_load_persisted_endpoint` reads, so `GET /endpoint` and the
/// embedding factory stay in sync.
fn endpoint_file() -> String {
    crate::pyos::path::join(
        &crate::pyos::path::join(&crate::core::constants::BASE_DIR, "data"),
        "embedding_endpoint.json",
    )
}

// ===========================================================================
// Module-level helpers
// ===========================================================================

/// `_load_custom_endpoint() -> dict` — load the saved custom embedding endpoint,
/// if any.
///
/// ```python
/// def _load_custom_endpoint() -> dict:
///     try:
///         if os.path.exists(_ENDPOINT_FILE):
///             return json.loads(Path(_ENDPOINT_FILE).read_text())
///     except Exception:
///         pass
///     return {}
/// ```
///
/// Returns an [`IndexMap`] so insertion/file order is preserved if it is ever
/// re-saved. A missing file or a parse failure both fall through to `{}`, exactly
/// like the broad Python `try/except`.
fn load_custom_endpoint() -> IndexMap<String, Value> {
    let file = endpoint_file();
    if Path::new(&file).exists() {
        if let Ok(text) = std::fs::read_to_string(&file) {
            if let Ok(Value::Object(map)) = serde_json::from_str::<Value>(&text) {
                return map.into_iter().collect();
            }
        }
    }
    IndexMap::new()
}

/// `_save_custom_endpoint(data)`.
///
/// ```python
/// def _save_custom_endpoint(data: dict):
///     Path(_ENDPOINT_FILE).parent.mkdir(parents=True, exist_ok=True)
///     Path(_ENDPOINT_FILE).write_text(json.dumps(data, indent=2))
/// ```
///
/// `json.dumps(data, indent=2)` and `serde_json::to_string_pretty` both emit
/// 2-space indentation, so the on-disk form matches byte-for-byte (no trailing
/// newline in either). The Python lets a write error propagate; here we mirror
/// the same eager creation + write (any IO error would surface to the caller,
/// which for the live path never happens).
fn save_custom_endpoint(data: &IndexMap<String, Value>) {
    let file = endpoint_file();
    // `Path(_ENDPOINT_FILE).parent.mkdir(parents=True, exist_ok=True)`
    if let Some(parent) = Path::new(&file).parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    // `Path(_ENDPOINT_FILE).write_text(json.dumps(data, indent=2))`
    let obj: serde_json::Map<String, Value> =
        data.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
    if let Ok(text) = serde_json::to_string_pretty(&Value::Object(obj)) {
        let _ = std::fs::write(&file, text);
    }
}

/// `_active_model()` — the currently configured fastembed model name.
///
/// ```python
/// def _active_model() -> str:
///     return os.environ.get("FASTEMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
/// ```
fn active_model() -> String {
    crate::pyos::getenv("FASTEMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
}

/// `_cache_dir()` — the fastembed cache directory. Delegates to
/// `embeddings::fastembed_cache_dir` so the admin panel and the loader agree.
fn cache_dir() -> String {
    crate::src::embeddings::fastembed_cache_dir()
}

/// `_model_cache_name(hf_source)` — `"models--" + hf_source.replace("/", "--")`.
fn model_cache_name(hf_source: &str) -> String {
    format!("models--{}", hf_source.replace('/', "--"))
}

/// `_is_downloaded(hf_source)` — is the model already cached?
///
/// ```python
/// model_dir = os.path.join(cache, _model_cache_name(hf_source))
/// if not os.path.isdir(model_dir): return False
/// snapshots = os.path.join(model_dir, "snapshots")
/// if os.path.isdir(snapshots): return any(os.listdir(snapshots))
/// blobs = os.path.join(model_dir, "blobs")
/// return os.path.isdir(blobs) and any(os.listdir(blobs))
/// ```
fn is_downloaded(hf_source: &str) -> bool {
    let model_dir = crate::pyos::path::join(&cache_dir(), &model_cache_name(hf_source));
    if !Path::new(&model_dir).is_dir() {
        return false;
    }
    let non_empty_dir = |p: &str| {
        std::fs::read_dir(p)
            .map(|mut it| it.next().is_some())
            .unwrap_or(false)
    };
    let snapshots = crate::pyos::path::join(&model_dir, "snapshots");
    if Path::new(&snapshots).is_dir() {
        return non_empty_dir(&snapshots);
    }
    let blobs = crate::pyos::path::join(&model_dir, "blobs");
    Path::new(&blobs).is_dir() && non_empty_dir(&blobs)
}

/// `_dir_size_mb(path)` — recursive byte total / (1024*1024), rounded to 1 dp.
/// `os.walk` + `os.path.getsize` with per-file `OSError` swallowed.
fn dir_size_mb(path: &str) -> f64 {
    let mut total: u64 = 0;
    for entry in walkdir::WalkDir::new(path).into_iter().flatten() {
        if entry.file_type().is_file() {
            if let Ok(meta) = entry.metadata() {
                total += meta.len();
            }
        }
    }
    let mb = total as f64 / (1024.0 * 1024.0);
    (mb * 10.0).round() / 10.0
}

/// The fastembed-rs `model_code` (HF source repo) for a model variant name, or
/// `None` if the name does not parse to a known `EmbeddingModel`.
fn model_code_for(variant_name: &str) -> Option<String> {
    let model = fastembed::EmbeddingModel::from_str(variant_name).ok()?;
    fastembed::TextEmbedding::get_model_info(&model)
        .ok()
        .map(|info| info.model_code.clone())
}

// ===========================================================================
// Admin gate (`Depends(require_admin)` on the router)
// ===========================================================================

/// `APIRouter(prefix="/api/embeddings", dependencies=[Depends(require_admin)])` —
/// the router-wide admin gate.
///
/// Python attaches `Depends(require_admin)` (core/middleware.py) to the whole
/// router, so EVERY handler under `/api/embeddings/...` runs the admin check
/// first. FastAPI evaluates router-level dependencies before the path operation;
/// in axum there is no router-level dependency, so the faithful equivalent is to
/// call this gate at the very top of each handler (before any other work — for
/// `set_endpoint`, before the form is even consumed conceptually; the gate runs
/// before the SSRF check and the health probe).
///
/// Reads the `X-Odysseus-Internal-Token` header + the stamped `current_user` and
/// delegates to the foundation [`auth_adapter::require_admin`], which wraps
/// `core/middleware.require_admin` and raises `HTTPException(403, "Admin only")`
/// on the reject arm.
fn require_admin(
    state: &AppState,
    headers: &HeaderMap,
    user: Option<&CurrentUser>,
) -> Result<(), HttpException> {
    let internal_header = headers
        .get(crate::core::middleware::INTERNAL_TOOL_HEADER)
        .and_then(|v| v.to_str().ok());
    let user = user.map(|u| u.0.as_str());
    auth_adapter::require_admin(state, internal_header, user)
}

// ===========================================================================
// Router factory (`setup_embedding_routes`)
// ===========================================================================

/// `setup_embedding_routes()` — `APIRouter(prefix="/api/embeddings",
/// dependencies=[Depends(require_admin)])`.
///
/// app.py registers this as include-router #16. The factory takes no parameters
/// (the Python `setup_embedding_routes()` captures nothing from `app.py`); deps
/// come from the `&'static` singletons reached directly in the handlers.
///
/// The router-level `Depends(require_admin)` is reproduced per-handler: each
/// handler takes `State<AppState>` + `HeaderMap` + `Option<Extension<CurrentUser>>`
/// and calls [`require_admin`] as its first statement (see the gate docs).
///
/// ## `{model_name:path}` -> axum catch-all wildcard
///
/// FastAPI's three `/models/{model_name:path}...` routes use the `:path` converter,
/// which matches `/`-containing model ids (e.g. `qdrant/all-MiniLM-L6-v2-onnx`):
///
/// ```text
///   POST   /models/{model_name:path}/download
///   GET    /models/{model_name:path}/status
///   DELETE /models/{model_name:path}
/// ```
///
/// axum 0.7's only multi-segment match is the trailing catch-all `*rest`, which
/// **cannot** be followed by a fixed suffix (`/models/*rest/download` panics at
/// router construction) and **cannot** coexist with a second distinct wildcard at
/// the same prefix (two `*name`s on `/models/*` panic on merge). So all three are
/// folded into ONE catch-all `/api/embeddings/models/*rest` carrying
/// `GET`/`POST`/`DELETE`, dispatched by method to the matching handler — the only
/// axum-expressible shape, and lossless for behaviour:
///
/// * `POST   /api/embeddings/models/<...>` -> [`download_model`] (the `.../download`
///   route). HONEST STUB `503`, so the captured tail (`<model>/download`) is unused.
/// * `GET    /api/embeddings/models/<...>` -> [`download_status`] (the `.../status`
///   route). HONEST STUB `503`, captured tail unused.
/// * `DELETE /api/embeddings/models/<...>` -> [`delete_model`]. Here the captured
///   `*rest` IS the `model_name` (the Python `:path` has no suffix), used by the
///   pre-fastembed active-model / downloading guards before the `503` fall-through.
///
/// `GET /api/embeddings/models` (the bare list endpoint) is a separate exact route
/// that coexists with the catch-all (axum allows an exact path alongside a wildcard
/// at the next segment).
pub fn setup_embedding_routes() -> Router<AppState> {
    Router::new()
        // `@router.get("/models")`
        .route("/api/embeddings/models", get(list_models))
        // The `{model_name:path}` trio folded into one method-dispatched catch-all:
        //   `@router.post("/models/{model_name:path}/download")`   -> POST
        //   `@router.get("/models/{model_name:path}/status")`      -> GET
        //   `@router.delete("/models/{model_name:path}")`          -> DELETE
        .route(
            "/api/embeddings/models/*rest",
            post(download_model)
                .get(download_status)
                .delete(delete_model),
        )
        // `@router.get("/endpoint")` + `@router.post("/endpoint")` + `@router.delete("/endpoint")`
        .route(
            "/api/embeddings/endpoint",
            get(get_endpoint).post(set_endpoint).delete(clear_endpoint),
        )
}

// ===========================================================================
// `/models/*` handlers — REAL over the `fastembed` crate (fastembed-rs)
// ===========================================================================

/// `@router.get("/models")` — `def list_models():`
///
/// Lists every fastembed-rs `EmbeddingModel` with its download/active/recommended
/// status, sorted active-first, then downloaded, then by `size_gb` — exactly the
/// Python `result.sort(key=lambda x: (not x["active"], not x["downloaded"],
/// x["size_gb"]))`. See the module docs for the documented drift (variant-name
/// ids, `size_gb == 0`).
///
/// Admin-gated (the router's `Depends(require_admin)`); the gate runs first, then
/// delegates to [`list_models_inner`] which holds the unchanged catalog logic.
async fn list_models(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    list_models_inner().await
}

async fn list_models_inner() -> Result<Response, HttpException> {
    use fastembed::TextEmbedding;

    // active = _active_model(); mapped through `map_model_name` to compare by enum.
    let active_enum = crate::src::embeddings::map_model_name(&active_model());
    let downloading = DOWNLOADING.lock().unwrap().clone();

    let mut result: Vec<Value> = Vec::new();
    for m in TextEmbedding::list_supported_models() {
        // The catalog id is the unique variant name; the HF source is model_code.
        let model_id = format!("{:?}", m.model);
        let hf_src = m.model_code.clone();
        let downloaded = is_downloaded(&hf_src);

        // cached_size = _dir_size_mb(model_path) if downloaded else None
        let cached_size = if downloaded {
            let model_path =
                crate::pyos::path::join(&cache_dir(), &model_cache_name(&hf_src));
            Some(dir_size_mb(&model_path))
        } else {
            None
        };

        result.push(json!({
            "model": model_id,
            "dim": m.dim,
            // fastembed-rs ModelInfo has no size_in_GB; Python `.get("size_in_GB", 0)`.
            "size_gb": 0,
            "description": m.description,
            "downloaded": downloaded,
            "downloading": downloading.contains(&model_id),
            "active": m.model == active_enum,
            "recommended": RECOMMENDED_MODELS.contains(&model_id.as_str()),
            "cached_size_mb": cached_size,
        }));
    }

    // result.sort(key=lambda x: (not active, not downloaded, size_gb)). size_gb is
    // a constant 0 here, so the effective order is active-first then downloaded.
    result.sort_by(|a, b| {
        let key = |v: &Value| {
            (
                !v["active"].as_bool().unwrap_or(false),
                !v["downloaded"].as_bool().unwrap_or(false),
                v["size_gb"].as_f64().unwrap_or(0.0),
            )
        };
        let (a1, a2, a3) = key(a);
        let (b1, b2, b3) = key(b);
        a1.cmp(&b1)
            .then(a2.cmp(&b2))
            .then(a3.partial_cmp(&b3).unwrap_or(std::cmp::Ordering::Equal))
    });
    Ok(Json(result).into_response())
}

/// `@router.post("/models/{model_name:path}/download")` —
/// `async def download_model(model_name: str):`
///
/// ```python
/// catalog = {m["model"]: m for m in TextEmbedding.list_supported_models()}
/// if model_name not in catalog: raise HTTPException(404, f"Unknown model: {model_name}")
/// hf_src = catalog[model_name].get("sources", {}).get("hf", "")
/// if hf_src and _is_downloaded(hf_src): return {"status": "already_downloaded", ...}
/// if model_name in _downloading: return {"status": "already_downloading", ...}
/// _downloading[model_name] = True
/// try: await run_in_executor(None, lambda: TextEmbedding(model_name, cache_dir=cache))
///      return {"status": "downloaded", "model": model_name}
/// except Exception as e: raise HTTPException(500, f"Download failed: {e}")
/// finally: _downloading.pop(model_name, None)
/// ```
///
/// The `*rest` capture is `<model_name>/download`; strip the suffix to recover the
/// `:path` value. `run_in_executor(None, ...)` -> `spawn_blocking` (the fastembed-rs
/// `try_new` does blocking HF downloads + ONNX init).
///
/// Admin-gated; the gate runs first, then delegates to [`download_model_inner`].
async fn download_model(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    axum::extract::Path(rest): axum::extract::Path<String>,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    download_model_inner(rest).await
}

async fn download_model_inner(rest: String) -> Result<Response, HttpException> {
    use fastembed::{InitOptions, TextEmbedding};

    let model_name = rest.strip_suffix("/download").unwrap_or(&rest).to_string();

    // Validate against the catalog (Python `if model_name not in catalog`).
    let model_enum = fastembed::EmbeddingModel::from_str(&model_name)
        .map_err(|_| HttpException::new(404, format!("Unknown model: {model_name}")))?;
    let hf_src = model_code_for(&model_name).unwrap_or_default();

    // if hf_src and _is_downloaded(hf_src): already_downloaded
    if !hf_src.is_empty() && is_downloaded(&hf_src) {
        return Ok(Json(json!({"status": "already_downloaded", "model": model_name}))
            .into_response());
    }

    // if model_name in _downloading: already_downloading  (+ claim the slot)
    {
        let mut dl = DOWNLOADING.lock().unwrap();
        if dl.contains(&model_name) {
            return Ok(Json(json!({"status": "already_downloading", "model": model_name}))
                .into_response());
        }
        dl.insert(model_name.clone());
    }

    let cache = cache_dir();
    let dl_result = tokio::task::spawn_blocking(move || {
        TextEmbedding::try_new(
            InitOptions::new(model_enum)
                .with_cache_dir(std::path::PathBuf::from(&cache)),
        )
        .map(|_| ())
        .map_err(|e| e.to_string())
    })
    .await;

    // finally: _downloading.pop(model_name, None)
    DOWNLOADING.lock().unwrap().remove(&model_name);

    match dl_result {
        Ok(Ok(())) => {
            Ok(Json(json!({"status": "downloaded", "model": model_name})).into_response())
        }
        Ok(Err(e)) => {
            logger::error(&format!("Failed to download {model_name}: {e}"));
            Err(HttpException::new(500, format!("Download failed: {e}")))
        }
        Err(e) => {
            logger::error(&format!("Failed to download {model_name}: {e}"));
            Err(HttpException::new(500, format!("Download failed: {e}")))
        }
    }
}

/// `@router.get("/models/{model_name:path}/status")` —
/// `def download_status(model_name: str):`
///
/// ```python
/// catalog = {m["model"]: m for ...}
/// if model_name not in catalog: raise HTTPException(404, f"Unknown model: {model_name}")
/// hf_src = catalog[model_name].get("sources", {}).get("hf", "")
/// downloaded = _is_downloaded(hf_src) if hf_src else False
/// return {"model": ..., "downloaded": ..., "downloading": model_name in _downloading}
/// ```
///
/// The `*rest` capture is `<model_name>/status`; strip the suffix.
///
/// Admin-gated; the gate runs first, then delegates to [`download_status_inner`].
async fn download_status(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    axum::extract::Path(rest): axum::extract::Path<String>,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    download_status_inner(rest).await
}

async fn download_status_inner(rest: String) -> Result<Response, HttpException> {
    let model_name = rest.strip_suffix("/status").unwrap_or(&rest).to_string();

    let hf_src = model_code_for(&model_name)
        .ok_or_else(|| HttpException::new(404, format!("Unknown model: {model_name}")))?;
    let downloaded = !hf_src.is_empty() && is_downloaded(&hf_src);
    let downloading = DOWNLOADING.lock().unwrap().contains(&model_name);

    Ok(Json(json!({
        "model": model_name,
        "downloaded": downloaded,
        "downloading": downloading,
    }))
    .into_response())
}

/// `@router.delete("/models/{model_name:path}")` — `def delete_model(model_name: str):`
///
/// ```python
/// if model_name == _active_model(): raise HTTPException(400, "Cannot delete the active embedding model")
/// if model_name in _downloading: raise HTTPException(400, "Model is currently downloading")
/// catalog = {m["model"]: m for ...}
/// if model_name not in catalog: raise HTTPException(404, f"Unknown model: {model_name}")
/// hf_src = catalog[model_name].get("sources", {}).get("hf", "")
/// if not hf_src: raise HTTPException(400, "No cache source for this model")
/// model_path = os.path.join(_cache_dir(), _model_cache_name(hf_src))
/// if not os.path.isdir(model_path): return {"deleted": False, "message": "Model not cached"}
/// shutil.rmtree(model_path); return {"deleted": True, "model": model_name}
/// ```
///
/// The `{model_name:path}` capture is the wildcard `*model_name`; axum delivers it
/// WITHOUT a leading slash. The active-model guard compares by enum (the env string
/// mapped through `map_model_name`), since the catalog id is the variant name.
///
/// Admin-gated; the gate runs first, then delegates to [`delete_model_inner`].
async fn delete_model(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    axum::extract::Path(model_name): axum::extract::Path<String>,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    delete_model_inner(model_name).await
}

async fn delete_model_inner(model_name: String) -> Result<Response, HttpException> {
    // if model_name == _active_model(): 400  (compared by enum — see module docs)
    let active_enum = crate::src::embeddings::map_model_name(&active_model());
    if fastembed::EmbeddingModel::from_str(&model_name) == Ok(active_enum) {
        return Err(HttpException::new(
            400,
            "Cannot delete the active embedding model",
        ));
    }
    // if model_name in _downloading: 400
    if DOWNLOADING.lock().unwrap().contains(&model_name) {
        return Err(HttpException::new(400, "Model is currently downloading"));
    }

    // if model_name not in catalog: 404
    let hf_src = model_code_for(&model_name)
        .ok_or_else(|| HttpException::new(404, format!("Unknown model: {model_name}")))?;
    // if not hf_src: 400 "No cache source for this model"
    if hf_src.is_empty() {
        return Err(HttpException::new(400, "No cache source for this model"));
    }

    let model_path = crate::pyos::path::join(&cache_dir(), &model_cache_name(&hf_src));
    if !Path::new(&model_path).is_dir() {
        return Ok(Json(json!({"deleted": false, "message": "Model not cached"}))
            .into_response());
    }

    // shutil.rmtree(model_path)
    std::fs::remove_dir_all(&model_path)
        .map_err(|e| HttpException::new(500, format!("Delete failed: {e}")))?;
    logger::info(&format!(
        "Deleted cached model: {model_name} ({model_path})"
    ));
    Ok(Json(json!({"deleted": true, "model": model_name})).into_response())
}

// ===========================================================================
// `/endpoint` handlers — PORT_NOW
// ===========================================================================

/// `@router.get("/endpoint")` — `def get_endpoint():`
///
/// ```python
/// def get_endpoint():
///     saved = _load_custom_endpoint()
///     current_url = os.environ.get("EMBEDDING_URL", "")
///     return {
///         "url": saved.get("url", current_url),
///         "model": saved.get("model", os.environ.get("EMBEDDING_MODEL", "")),
///         "active": bool(saved.get("url") or current_url),
///     }
/// ```
///
/// `saved.get("url", current_url)`: the saved value when the `url` KEY is present
/// (even if `""`), else the env `current_url`. `bool(saved.get("url") or current_url)`
/// is truthy iff either is a non-empty string.
///
/// Admin-gated; the gate runs first, then delegates to [`get_endpoint_inner`].
async fn get_endpoint(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    get_endpoint_inner().await
}

async fn get_endpoint_inner() -> Result<Response, HttpException> {
    let saved = load_custom_endpoint();
    // `current_url = os.environ.get("EMBEDDING_URL", "")`
    let current_url = crate::pyos::getenv("EMBEDDING_URL", "");

    // `saved.get("url", current_url)` — dict.get uses the default ONLY when the key
    // is ABSENT, so a present `url` (even an empty string) shadows `current_url`.
    let saved_url_value = saved.get("url");
    let url = match saved_url_value {
        Some(v) => json_to_str(v),
        None => current_url.clone(),
    };
    // `saved.get("model", os.environ.get("EMBEDDING_MODEL", ""))`
    let model = match saved.get("model") {
        Some(v) => json_to_str(v),
        None => crate::pyos::getenv("EMBEDDING_MODEL", ""),
    };
    // `bool(saved.get("url") or current_url)` — `saved.get("url")` (no default) is
    // `None` when absent; `None or current_url` -> `current_url`; truthy iff the
    // chosen value is a non-empty string.
    let saved_url_truthy = saved_url_value.map(is_truthy).unwrap_or(false);
    let active = saved_url_truthy || !current_url.is_empty();

    Ok(Json(json!({
        "url": url,
        "model": model,
        "active": active,
    }))
    .into_response())
}

/// `class _SetEndpointForm` — the `Form(...)` body of `POST /endpoint`.
///
/// ```python
/// def set_endpoint(url: str = Form(...), model: str = Form("")):
/// ```
///
/// `url` is REQUIRED (`Form(...)`): an absent `url` field is a 422 (FastAPI's
/// request-validation error, mirrored by `axum::Form`'s deserialize failure). A
/// present-but-empty `url=` deserialises fine, then hits the explicit `400 "URL is
/// required"` check in the handler. `model` defaults to `""` (`Form("")`).
#[derive(Debug, Deserialize)]
struct SetEndpointForm {
    url: String,
    #[serde(default)]
    model: String,
}

/// `@router.post("/endpoint")` — `def set_endpoint(url: str = Form(...), model: str = Form("")):`
///
/// ```python
/// url = url.strip()
/// if not url:
///     raise HTTPException(400, "URL is required")
/// # SSRF hardening: validate the user-supplied URL before any outbound request.
/// from src.url_safety import check_outbound_url
/// ok, reason = check_outbound_url(
///     url,
///     block_private=os.getenv("EMBEDDING_BLOCK_PRIVATE_IPS", "false").lower() == "true",
/// )
/// if not ok:
///     raise HTTPException(400, f"Rejected endpoint URL: {reason}")
/// try:
///     import httpx
///     resp = httpx.post(url, json={"input": ["test"], "model": model or "test"}, timeout=10)
///     resp.raise_for_status()
/// except Exception as e:
///     raise HTTPException(400, f"Endpoint unreachable: {e}")
/// data = {"url": url}
/// if model:
///     data["model"] = model
/// _save_custom_endpoint(data)
/// os.environ["EMBEDDING_URL"] = url
/// if model:
///     os.environ["EMBEDDING_MODEL"] = model
/// import src.rag_singleton as _rs
/// _rs.rag_instance = None
/// _rs._last_attempt = 0
/// try: from src.embeddings import reset_http_embed_state; reset_http_embed_state()
/// except Exception: pass
/// try: from src.chroma_client import reset_client; reset_client()
/// except Exception: pass
/// logger.info(f"Custom embedding endpoint set: {url}")
/// return {"success": True, "url": url, "model": model}
/// ```
///
/// Admin-gated (the router's `Depends(require_admin)`); the gate runs first, BEFORE
/// the SSRF check and the outbound health probe. The body logic lives in
/// [`set_endpoint_inner`] so the co-located tests can exercise the SSRF rejection
/// (and the validation/save path) without a full `AppState`.
async fn set_endpoint(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
    Form(form): Form<SetEndpointForm>,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    set_endpoint_inner(form).await
}

async fn set_endpoint_inner(form: SetEndpointForm) -> Result<Response, HttpException> {
    // `url = url.strip()`
    let url = form.url.trim().to_string();
    let model = form.model;
    // `if not url: raise HTTPException(400, "URL is required")`
    if url.is_empty() {
        return Err(HttpException::new(400, "URL is required"));
    }

    // SSRF hardening: validate the user-supplied URL before any outbound request.
    // Local-first means loopback/LAN endpoints are allowed by default; non-HTTP(S)
    // schemes and the cloud metadata range are always rejected. Set
    // `EMBEDDING_BLOCK_PRIVATE_IPS=true` for full lockdown.
    // `ok, reason = check_outbound_url(url, block_private=os.getenv(...) == "true")`
    let block_private =
        crate::pyos::getenv("EMBEDDING_BLOCK_PRIVATE_IPS", "false").to_lowercase() == "true";
    let (ok, reason) = crate::src::url_safety::check_outbound_url(&url, block_private);
    if !ok {
        // `raise HTTPException(400, f"Rejected endpoint URL: {reason}")`
        return Err(HttpException::new(
            400,
            format!("Rejected endpoint URL: {reason}"),
        ));
    }

    // Quick health check — `httpx.post(url, json=..., timeout=10); resp.raise_for_status()`.
    // Any error (transport OR non-2xx) -> `HTTPException(400, f"Endpoint unreachable: {e}")`.
    // The Python passes `model or "test"` for the `model` field of the probe body.
    let probe_model = if model.is_empty() { "test" } else { &model };
    if let Err(e) = health_check(&url, probe_model).await {
        return Err(HttpException::new(400, format!("Endpoint unreachable: {e}")));
    }

    // `data = {"url": url}; if model: data["model"] = model`
    let mut data: IndexMap<String, Value> = IndexMap::new();
    data.insert("url".to_string(), Value::String(url.clone()));
    if !model.is_empty() {
        data.insert("model".to_string(), Value::String(model.clone()));
    }
    // `_save_custom_endpoint(data)`
    save_custom_endpoint(&data);

    // `os.environ["EMBEDDING_URL"] = url`
    crate::pyos::set_var("EMBEDDING_URL", &url);
    // `if model: os.environ["EMBEDDING_MODEL"] = model`
    if !model.is_empty() {
        crate::pyos::set_var("EMBEDDING_MODEL", &model);
    }

    // `import src.rag_singleton as _rs; _rs.rag_instance = None; _rs._last_attempt = 0`
    // SKIPPED: no public reset hook for the (disabled, always-`None`) rag_singleton
    // statics; nulling them is a no-op. See the module docstring.

    // `try: reset_http_embed_state() except Exception: pass`
    crate::src::embeddings::reset_http_embed_state();
    // `try: reset_client() except Exception: pass`
    crate::src::chroma_client::reset_client();

    // `logger.info(f"Custom embedding endpoint set: {url}")`
    logger::info(&format!("Custom embedding endpoint set: {url}"));
    Ok(Json(json!({
        "success": true,
        "url": url,
        "model": model,
    }))
    .into_response())
}

/// `@router.delete("/endpoint")` — `def clear_endpoint():`
///
/// ```python
/// def clear_endpoint():
///     if os.path.exists(_ENDPOINT_FILE):
///         os.remove(_ENDPOINT_FILE)
///     os.environ.pop("EMBEDDING_URL", None)
///     os.environ.pop("EMBEDDING_MODEL", None)
///     import src.rag_singleton as _rs
///     _rs.rag_instance = None
///     _rs._last_attempt = 0
///     try: from src.embeddings import reset_http_embed_state; reset_http_embed_state()
///     except Exception: pass
///     try: from src.chroma_client import reset_client; reset_client()
///     except Exception: pass
///     logger.info("Custom embedding endpoint cleared, reverting to local fastembed")
///     return {"success": True}
/// ```
///
/// Admin-gated; the gate runs first, then delegates to [`clear_endpoint_inner`].
async fn clear_endpoint(
    State(state): State<AppState>,
    headers: HeaderMap,
    user: Option<Extension<CurrentUser>>,
) -> Result<Response, HttpException> {
    require_admin(&state, &headers, user.as_ref().map(|Extension(u)| u))?;
    clear_endpoint_inner().await
}

async fn clear_endpoint_inner() -> Result<Response, HttpException> {
    let file = endpoint_file();
    // `if os.path.exists(_ENDPOINT_FILE): os.remove(_ENDPOINT_FILE)`
    if Path::new(&file).exists() {
        let _ = std::fs::remove_file(&file);
    }

    // `os.environ.pop("EMBEDDING_URL", None)` / `os.environ.pop("EMBEDDING_MODEL", None)`.
    // `pyos` has no `pop`; `std::env::remove_var` is the faithful analogue (a no-op
    // when the var is unset, exactly like `dict.pop(k, None)`).
    std::env::remove_var("EMBEDDING_URL");
    std::env::remove_var("EMBEDDING_MODEL");

    // `import src.rag_singleton as _rs; _rs.rag_instance = None; _rs._last_attempt = 0`
    // SKIPPED (no public reset hook; no-op on the disabled singleton). See docstring.

    // `try: reset_http_embed_state() except Exception: pass`
    crate::src::embeddings::reset_http_embed_state();
    // `try: reset_client() except Exception: pass`
    crate::src::chroma_client::reset_client();

    // `logger.info("Custom embedding endpoint cleared, reverting to local fastembed")`
    logger::info("Custom embedding endpoint cleared, reverting to local fastembed");
    Ok(Json(json!({ "success": true })).into_response())
}

// ===========================================================================
// Small faithful helpers
// ===========================================================================

/// Run the `POST /endpoint` health check:
/// `resp = httpx.post(url, json={"input": ["test"], "model": probe_model}, timeout=10); resp.raise_for_status()`.
///
/// Returns `Ok(())` on a 2xx, else the error (transport failure OR a non-2xx
/// status, which `error_for_status` converts to an `Err` — the `reqwest`
/// analogue of `httpx`'s `raise_for_status()`). The caller wraps any error in the
/// `400 "Endpoint unreachable: {e}"` detail (the `{e}` text differs from httpx; see
/// the module docstring).
async fn health_check(url: &str, probe_model: &str) -> Result<(), reqwest::Error> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(10))
        .build()?;
    let resp = client
        .post(url)
        .json(&json!({ "input": ["test"], "model": probe_model }))
        .send()
        .await?;
    // `resp.raise_for_status()` — raise on a 4xx/5xx status.
    resp.error_for_status()?;
    Ok(())
}

/// Render a JSON value the way the Python handler echoes a `dict.get(...)` result
/// when the stored value is (almost always) a string. The on-disk config only ever
/// stores string `url`/`model` values, so the common path is `Value::String -> the
/// inner string`. For robustness against a hand-edited config, a non-string value
/// is rendered with `to_string` (its JSON form) rather than panicking.
fn json_to_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// Python truthiness for the JSON value behind `bool(saved.get("url"))`: a present,
/// non-empty string (the only shape `url` ever takes on disk) is truthy; `None`/
/// `""`/missing is falsy.
fn is_truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::String(s) => !s.is_empty(),
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::to_bytes;
    use axum::http::StatusCode;

    async fn body_json(resp: Response) -> Value {
        let bytes = to_bytes(resp.into_body(), usize::MAX).await.unwrap();
        serde_json::from_slice(&bytes).unwrap()
    }

    #[tokio::test]
    async fn list_models_returns_the_fastembed_catalog() {
        // REAL over fastembed-rs: `list_supported_models()` is a static catalog
        // (no network), so this is safe + deterministic. We assert structure, the
        // active-first sort, and the recommended flag — not the env-dependent
        // `downloaded` value (which only READS the cache dir, never mutates it).
        // Calls the post-gate inner body (the admin gate is exercised separately in
        // `require_admin_gate_*`); the handler wrapper merely runs the gate then this.
        let resp = list_models_inner().await.unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
        let body = body_json(resp).await;
        let arr = body.as_array().expect("array");
        assert!(!arr.is_empty(), "catalog must be non-empty");
        for m in arr {
            for k in [
                "model",
                "dim",
                "size_gb",
                "description",
                "downloaded",
                "downloading",
                "active",
                "recommended",
                "cached_size_mb",
            ] {
                assert!(m.get(k).is_some(), "entry missing key {k}: {m}");
            }
            assert_eq!(m["size_gb"], json!(0)); // fastembed-rs has no size_in_GB
        }
        // The active model (env FASTEMBED_MODEL -> map_model_name) sorts first and
        // is the only `active == true` entry.
        let active_variant =
            format!("{:?}", crate::src::embeddings::map_model_name(&active_model()));
        assert_eq!(arr[0]["model"], json!(active_variant));
        assert_eq!(arr[0]["active"], json!(true));
        assert_eq!(arr.iter().filter(|m| m["active"] == json!(true)).count(), 1);
        // A known recommended variant is flagged.
        let small = arr
            .iter()
            .find(|m| m["model"] == json!("BGESmallENV15"))
            .expect("BGESmallENV15 in catalog");
        assert_eq!(small["recommended"], json!(true));
    }

    #[tokio::test]
    async fn download_unknown_model_is_404() {
        // from_str fails before any download is attempted (no network touched).
        // Post-gate inner body (admin gate covered in `require_admin_gate_*`).
        let err = download_model_inner("no-such-model-xyz/download".to_string())
            .await
            .unwrap_err();
        assert_eq!(err.status_code, 404);
        assert!(err.detail.starts_with("Unknown model:"));
    }

    #[tokio::test]
    async fn download_status_unknown_404_known_reports_shape() {
        // Unknown -> 404.
        let err = download_status_inner("nope-xyz/status".to_string())
            .await
            .unwrap_err();
        assert_eq!(err.status_code, 404);

        // Known -> 200 with the {model, downloaded, downloading} shape (read-only
        // cache probe; `downloaded` is env-dependent so we only check the keys).
        let resp = download_status_inner("AllMiniLML6V2/status".to_string())
            .await
            .unwrap();
        let body = body_json(resp).await;
        assert_eq!(body["model"], json!("AllMiniLML6V2"));
        assert_eq!(body["downloading"], json!(false));
        assert!(body["downloaded"].is_boolean());
    }

    #[tokio::test]
    async fn delete_model_guards() {
        // Active model (by VARIANT NAME, the catalog id) -> 400 before any cache
        // touch. The active env string maps through map_model_name to the enum.
        let active_variant =
            format!("{:?}", crate::src::embeddings::map_model_name(&active_model()));
        let err = delete_model_inner(active_variant).await.unwrap_err();
        assert_eq!(err.status_code, 400);
        assert_eq!(err.detail, "Cannot delete the active embedding model");

        // Unknown, non-active model -> 404 (never reaches the rmtree path).
        let err = delete_model_inner("unknown-model-xyz".to_string())
            .await
            .unwrap_err();
        assert_eq!(err.status_code, 404);
        assert!(err.detail.starts_with("Unknown model:"));
    }

    #[test]
    fn setendpointform_url_required_model_defaults_empty() {
        // The serde contract behind `Form(...)`: `url` required, `model` defaults to
        // "" (`Form("")`). Tested via the same `Deserialize` impl `axum::Form` drives
        // (the field requiredness/default is deserializer-format-independent).
        let f: SetEndpointForm =
            serde_json::from_str(r#"{"url":"http://localhost:11434/v1/embeddings"}"#).unwrap();
        assert_eq!(f.url, "http://localhost:11434/v1/embeddings");
        assert_eq!(f.model, "");
        // Explicit model is kept.
        let f2: SetEndpointForm =
            serde_json::from_str(r#"{"url":"http://x/","model":"nomic-embed-text"}"#).unwrap();
        assert_eq!(f2.model, "nomic-embed-text");
        // Missing `url` is a deserialize error (FastAPI 422 for the required field).
        assert!(serde_json::from_str::<SetEndpointForm>(r#"{"model":"foo"}"#).is_err());
    }

    #[test]
    fn is_truthy_matches_python_bool() {
        assert!(!is_truthy(&Value::Null));
        assert!(!is_truthy(&Value::String(String::new())));
        assert!(is_truthy(&Value::String("http://x".to_string())));
    }

    #[test]
    fn json_to_str_unwraps_strings() {
        assert_eq!(json_to_str(&Value::String("abc".to_string())), "abc");
        // Non-string falls back to JSON text (defensive; never on the live path).
        assert_eq!(json_to_str(&Value::Bool(true)), "true");
    }

    #[test]
    fn require_admin_gate_allows_when_auth_disabled_rejects_anonymous() {
        // The router-wide `Depends(require_admin)` resolves through
        // `core::middleware::require_admin` (what `auth_adapter::require_admin`,
        // and thus the module `require_admin`, wraps). An "auth-disabled state"
        // (`auth_enabled == false`) admits every caller — the shape the other
        // handler-body tests rely on. The gate itself does not touch any
        // embedding state, so testing the decision here (without standing up the
        // 18-Arc `AppState`) is the faithful unit-level check.
        let auth = crate::core::auth::AuthManager::new();

        // auth-disabled -> Ok even for an anonymous caller (no internal header).
        assert!(crate::core::middleware::require_admin(&auth, false, None, None).is_ok());

        // auth ENABLED + unconfigured -> reject (the gate fails closed). Only assert
        // this when the test data dir is genuinely unconfigured (matches the
        // auth_adapter tests' own guard).
        if !auth.is_configured() {
            assert!(crate::core::middleware::require_admin(&auth, true, None, None).is_err());
        }

        // The internal-tool token bypass is honored regardless of auth state.
        let tok = crate::core::middleware::INTERNAL_TOOL_TOKEN.as_str();
        assert!(crate::core::middleware::require_admin(&auth, true, Some(tok), None).is_ok());
    }

    #[tokio::test]
    async fn set_endpoint_rejects_unsafe_url_before_probe() {
        // SSRF hardening: a non-HTTP(S) scheme is rejected by `check_outbound_url`
        // BEFORE any DNS resolution or outbound probe, so this is network-free and
        // deterministic regardless of `EMBEDDING_BLOCK_PRIVATE_IPS`. The detail is
        // `f"Rejected endpoint URL: {reason}"` (400).
        let form = SetEndpointForm {
            url: "ftp://example.com/embeddings".to_string(),
            model: String::new(),
        };
        let err = set_endpoint_inner(form).await.unwrap_err();
        assert_eq!(err.status_code, 400);
        assert!(
            err.detail.starts_with("Rejected endpoint URL:"),
            "unexpected detail: {}",
            err.detail
        );
        // The reason text is the `check_outbound_url` scheme message.
        assert!(err.detail.contains("scheme must be http or https"));

        // An empty/whitespace URL still hits the pre-existing "URL is required"
        // 400 (the strip + empty check runs before the SSRF validation).
        let blank = SetEndpointForm {
            url: "   ".to_string(),
            model: String::new(),
        };
        let err = set_endpoint_inner(blank).await.unwrap_err();
        assert_eq!(err.status_code, 400);
        assert_eq!(err.detail, "URL is required");
    }
}
