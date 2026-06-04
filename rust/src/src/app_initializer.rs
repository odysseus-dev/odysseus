// src/app_initializer.rs  <- src/app_initializer.py
//! Initialize all application components and dependencies.
//!
//! PORT_PARTIAL (web). Faithful port of `src/app_initializer.py`'s
//! [`create_directories`] + [`initialize_managers`]. The Python returns a
//! heterogeneous `Dict[str, Any]` of live manager objects; the Rust analogue is
//! the concrete [`AppManagers`] struct (Arc-wrapped live objects, NOT a serde
//! map) so callers keep static types.
//!
//! ## The concrete-SessionManager global (deviation from Python's `ai_interaction`)
//! Python wires `core.models.set_session_manager(session_manager)` to enable
//! `Session.add_message()` persistence, and elsewhere `ai_interaction` keeps a
//! module global for the *concrete* manager (used by the background monitor). In
//! the Rust port:
//! * `core::models::set_session_manager` takes only an
//!   `Arc<dyn SessionPersistence>` (the narrow `_persist_message` capability) —
//!   consumers that need the *concrete* [`SessionManager`] (e.g. the
//!   `bg_monitor` follow-up drainer, which calls `get_session` /
//!   `get_context_messages` / `add_message`) cannot recover it from that trait
//!   object.
//! * `ai_interaction.rs` exposes only `dispatch_ai_tool` (no
//!   `get_session_manager`), and `core::models::session_manager()` is private.
//!
//! The NEW process-global holding the concrete `Arc<SessionManager>` lives in
//! [`crate::src::bg_monitor`] (its primary consumer): `bg_monitor` owns
//! `set_session_manager` / `get_session_manager`, gated `cfg(all(web, unix))` to
//! match the rest of that module. `initialize_managers` sets BOTH globals — the
//! `core::models` trait-object one (for `Session.add_message` persistence) AND,
//! on `unix` builds, `bg_monitor::set_session_manager` (for the monitor).
//! Documented deviation from the Python `ai_interaction` global home. On
//! non-`unix` `web` builds `bg_monitor` does not exist (its `nix` subprocess
//! layer is Unix-only), so the concrete global is simply absent there too.
//!
//! ## Wired collaborators
//! * **SkillsManager** — the real `crate::services::memory::skills::SkillsManager`
//!   is constructed (`SkillsManager(DATA_DIR)`), wrapped in a
//!   [`SkillsIndexAdapter`] (impl [`SkillsIndex`]), and passed as `ChatProcessor`'s
//!   skills collaborator, so the agent-mode skills-index block fires exactly like
//!   Python. It is also exposed on [`AppManagers::skills_manager`] (Python's
//!   `"skills_manager"` dict entry).
//! * **`update_search_config`** — wired to the real
//!   `crate::src::search::update_search_config`: a saved Brave API key is stashed
//!   into the `SEARCH_CONFIG` global, just like Python.
//!
//! ## Honest deviations
//! * **`rag_manager._model` duck-type** — Python shares the RAG embedding model
//!   with `MemoryVectorStore` via `getattr(rag_manager, '_model', None)`. The
//!   ported `RagManager` trait has no `_model` accessor, so `None` is passed to
//!   `MemoryVectorStore::new` (which lazily builds its own embedding client) —
//!   the same end state as the `embedding_model=None` Python path.
//!
//! ## Collaborator wiring
//! * `ChatProcessor::new` takes the duck-typed collaborators as narrow traits
//!   (`RagSearch` / `MemoryVectorSearch` / `SkillsIndex`).
//!   * **`memory_vector` IS wired.** Python passes the live store through
//!     (`ChatProcessor(..., memory_vector=memory_vector, ...)`), so chat-time
//!     semantic memory search runs whenever the store is healthy. When
//!     `initialize_managers` builds a healthy [`MemoryVectorStore`] we wrap it in
//!     [`MemoryVectorSearchAdapter`] (which bridges the store's `async` `search`
//!     into the sync [`MemoryVectorSearch`] trait via
//!     `block_in_place` + `Handle::block_on`, falling back to a fresh
//!     current-thread runtime off-runtime). When the store DEGRADES it is already
//!     `None` here (Python sets `memory_vector = None`), so `None` is passed —
//!     matching the Python degraded path.
//!   * `SkillsIndex` IS wired (see "Wired collaborators" above) to the real
//!     `SkillsManager` via [`SkillsIndexAdapter`].
//!   * `RagSearch` stays `None`: `rag_vector::VectorRAG` does not impl `RagSearch`
//!     and the application RAG singleton is disabled on the live path
//!     (`rag_singleton::get_rag_manager()` -> `None`, so Python's `rag_manager`
//!     is `None` too).
//! * `ChatHandler::new` stores `chat_processor` / `research_handler` as
//!   `Arc<dyn Collaborator>` but never calls them (the route layer uses the
//!   concrete objects). `ChatProcessor` / `ResearchHandler` do not implement the
//!   marker `Collaborator` trait, so a ZST [`CollaboratorStub`] is passed into
//!   those uncalled slots while the *concrete* `Arc<ChatProcessor>` /
//!   `Arc<ResearchHandler>` are stored on [`AppManagers`] for callers. No
//!   observable difference (the stored-but-uncalled collaborators have no effect
//!   inside `ChatHandler`).
//!
//! Always compiled (no cargo feature flags): it wires the manager surface
//! (`MemoryVectorStore`, `PersonalDocsManager`, `ChatProcessor`,
//! `ResearchHandler`, `ChatHandler`, `ModelDiscovery`, `UploadHandler`).

use std::sync::Arc;

use crate::core::models::set_session_manager;
use crate::core::session_manager::SessionManager;
use crate::error::PyResult;
use crate::pylog as logger;
use crate::pyos as os;
use crate::src::api_key_manager::APIKeyManager;
use crate::src::chat_handler::{ChatHandler, Collaborator};
use crate::src::chat_processor::{ChatProcessor, MemoryVectorSearch, SkillsIndex};
use crate::src::constants::{
    DATA_DIR, DEFAULT_HOST, OPENAI_API_KEY, PERSONAL_DIR, RUNBOOK_DIR, UPLOAD_DIR,
};
use crate::src::memory::MemoryManager;
use crate::src::memory_vector::MemoryVectorStore;
use crate::src::model_discovery::ModelDiscovery;
use crate::src::personal_docs::{PersonalDocsManager, RagManager};
use crate::src::preset_manager::PresetManager;
use crate::src::research_handler::ResearchHandler;
use crate::src::upload_handler::UploadHandler;

// ---------------------------------------------------------------------------
// ChatHandler collaborator stub
// ---------------------------------------------------------------------------

/// A zero-sized [`Collaborator`] for the `ChatHandler` slots that `__init__`
/// stores but this module never calls (`chat_processor` / `research_handler`).
/// The concrete objects live on [`AppManagers`]; the route layer uses those.
struct CollaboratorStub;
impl Collaborator for CollaboratorStub {}

// ---------------------------------------------------------------------------
// MemoryVectorStore -> ChatProcessor::MemoryVectorSearch bridge
// ---------------------------------------------------------------------------

/// Adapter wiring a live [`MemoryVectorStore`] into `ChatProcessor`'s
/// [`MemoryVectorSearch`] collaborator slot.
///
/// Python passes the store straight through
/// (`ChatProcessor(..., memory_vector=memory_vector, ...)`) and `_hybrid_retrieve`
/// calls `self.memory_vector.search(message, k=...)` synchronously. In the Rust
/// port `MemoryVectorStore::search` is `async` (its `EmbeddingClient::encode` is
/// an HTTP round-trip) while `ChatProcessor`'s `MemoryVectorSearch::search` is
/// `sync` (it is driven from the sync `hybrid_retrieve`). This adapter bridges
/// the async store into the sync trait.
///
/// ## Async->sync bridge
/// `build_context_preface` (the live caller of `hybrid_retrieve`) runs inside the
/// `#[tokio::main]` multi-threaded runtime, so we drive the future with
/// `tokio::task::block_in_place` + `Handle::block_on` — the documented way to run
/// a future to completion from a sync function on a multi-thread worker without
/// stalling the whole runtime. Outside any runtime (e.g. unit tests calling
/// `hybrid_retrieve` directly) we fall back to a fresh current-thread runtime
/// (the `event_bus::fire_event` precedent). `block_in_place` requires the
/// multi-thread flavor, so we only reach for it when a runtime *and* a
/// `try_current` handle are present, and the current-thread case never hits it.
///
/// ## Error handling vs Python
/// `MemoryVectorSearch::search` is infallible (`-> Vec<Value>`), but the async
/// `MemoryVectorStore::search` is `PyResult` (its `encode` raises on a downed
/// endpoint). Python's `_hybrid_retrieve` does NOT wrap the vector call in
/// `try/except`, so an embedding failure would propagate out of
/// `build_context_preface` there. The ported trait is fixed as infallible, so we
/// degrade instead: log a warning and return `[]` (no vector scores). The
/// observable retrieval still runs on the keyword/BM25 path — the same memories
/// surface, just without the vector blend — which is strictly more available
/// than the Python raise, and matches how every other embedding-backed path in
/// the port degrades when the endpoint is down.
struct MemoryVectorSearchAdapter {
    store: Arc<MemoryVectorStore>,
}

impl MemoryVectorSearch for MemoryVectorSearchAdapter {
    fn healthy(&self) -> bool {
        // `self.memory_vector.healthy` — the readiness property.
        self.store.healthy()
    }

    fn search(&self, query: &str, k: usize) -> Vec<serde_json::Value> {
        // `self.memory_vector.search(message, k=...)`. The store takes `k: i64`.
        let k = i64::try_from(k).unwrap_or(i64::MAX);
        let store = self.store.clone();
        let query = query.to_string();
        let fut = async move { store.search(&query, k).await };

        // Drive the async search to completion from this sync context.
        let result = match tokio::runtime::Handle::try_current() {
            // Inside the (multi-threaded) tokio runtime: hand off to a blocking
            // section so we don't park a worker, then block on the future.
            Ok(handle) => tokio::task::block_in_place(|| handle.block_on(fut)),
            // No running runtime (tests / sync callers): spin one up.
            Err(_) => match tokio::runtime::Builder::new_current_thread().enable_all().build() {
                Ok(rt) => rt.block_on(fut),
                Err(e) => {
                    logger::warning(&format!(
                        "memory vector search: failed to build runtime: {e}"
                    ));
                    return Vec::new();
                }
            },
        };

        match result {
            // Map each `MemoryHit` to the `{"memory_id", "score"}` dict the
            // ChatProcessor consumes.
            Ok(hits) => hits.iter().map(|h| h.to_value()).collect(),
            Err(e) => {
                // Embedding endpoint went down mid-call: degrade to no vector
                // scores (keyword retrieval still runs).
                logger::warning(&format!("memory vector search failed: {e}"));
                Vec::new()
            }
        }
    }
}

// ---------------------------------------------------------------------------
// SkillsManager -> ChatProcessor::SkillsIndex bridge
// ---------------------------------------------------------------------------

/// Adapter wiring the live [`crate::services::memory::skills::SkillsManager`]
/// into `ChatProcessor`'s [`SkillsIndex`] collaborator slot.
///
/// Python passes the manager straight through
/// (`ChatProcessor(..., skills_manager=skills_manager)`) and the agent-mode
/// skills-index block calls `self.skills_manager.index_for(owner=owner)`. The
/// real `SkillsManager::index_for` is sync (disk-backed `SKILL.md` scan), so no
/// async bridge is needed; the adapter only narrows the call to the trait's
/// `index_for(owner)` shape (passing `active_toolsets=None`, `platform=None`,
/// matching the Python call which only supplies `owner`) and maps the returned
/// `Vec<Map>` to the `Vec<Value>` the trait expects.
struct SkillsIndexAdapter {
    manager: Arc<crate::services::memory::skills::SkillsManager>,
}

impl SkillsIndex for SkillsIndexAdapter {
    fn index_for(&self, owner: Option<&str>) -> PyResult<Vec<serde_json::Value>> {
        // `self.skills_manager.index_for(owner=owner)` — toolsets/platform default
        // to None (Python supplies only `owner` here). `index_for` never raises.
        let rows = self.manager.index_for(owner, None, None);
        Ok(rows.into_iter().map(serde_json::Value::Object).collect())
    }
}

// ---------------------------------------------------------------------------
// AppManagers — the live wiring result (Python's dict, statically typed)
// ---------------------------------------------------------------------------

/// All initialized manager / handler instances.
///
/// This is the Rust analogue of Python's `Dict[str, Any]` return value, but with
/// live Arc-wrapped objects instead of a heterogeneous map. The two vestigial
/// dict entries are NOT mirrored as fields:
/// * `current_presets` (a copy of `preset_manager.presets`) — read it off
///   `preset_manager` directly.
/// * `PERSONAL_INDEX` (a copy of `personal_docs_manager.index`) — owned by
///   `personal_docs_manager` (no public accessor was added just for this dead
///   mirror).
pub struct AppManagers {
    /// `"memory_manager"`.
    pub memory_manager: Arc<MemoryManager>,
    /// `"memory_vector"` — `None` when DEGRADED (ChromaDB/embedding unavailable).
    pub memory_vector: Option<Arc<MemoryVectorStore>>,
    /// `"skills_manager"` — the real disk-backed `SkillsManager`.
    pub skills_manager: Arc<crate::services::memory::skills::SkillsManager>,
    /// `"session_manager"`.
    pub session_manager: Arc<SessionManager>,
    /// `"upload_handler"`.
    pub upload_handler: Arc<UploadHandler>,
    /// `"personal_docs_manager"`.
    pub personal_docs_manager: Arc<PersonalDocsManager>,
    /// `"api_key_manager"`.
    pub api_key_manager: Arc<APIKeyManager>,
    /// `"preset_manager"`. `Mutex`-wrapped because `PresetManager`'s mutating
    /// methods take `&mut self` (`load`/`save`/`update_custom`/…), and app.py
    /// shares the ONE instance across `setup_preset_routes(preset_manager)` and
    /// `setup_backup_routes(..., preset_manager, ...)` (both reach for it through
    /// `app.state`/the closure). The Rust `AppState.preset_manager` field is this
    /// same `Arc<Mutex<…>>`, so the routers share one live instance — instead of a
    /// throwaway built in `run()`.
    pub preset_manager: Arc<tokio::sync::Mutex<PresetManager>>,
    /// `"chat_processor"`.
    pub chat_processor: Arc<ChatProcessor>,
    /// `"research_handler"`.
    pub research_handler: Arc<ResearchHandler>,
    /// `"chat_handler"`.
    pub chat_handler: Arc<ChatHandler<UploadHandler>>,
    /// `"model_discovery"`.
    pub model_discovery: Arc<ModelDiscovery>,
}

// ---------------------------------------------------------------------------
// create_directories
// ---------------------------------------------------------------------------

/// Create necessary directories if they don't exist.
///
/// `for directory in (DATA_DIR, PERSONAL_DIR, RUNBOOK_DIR, UPLOAD_DIR):
/// os.makedirs(directory, exist_ok=True)`. The `src.constants` paths are
/// source-relative (they ignore `ODYSSEUS_DATA_DIR`), matching the Python
/// import.
pub fn create_directories() {
    for directory in [
        DATA_DIR.as_str(),
        PERSONAL_DIR.as_str(),
        RUNBOOK_DIR.as_str(),
        UPLOAD_DIR.as_str(),
    ] {
        // os.makedirs(directory, exist_ok=True)
        let _ = os::makedirs(directory, true);
    }
}

// ---------------------------------------------------------------------------
// initialize_managers
// ---------------------------------------------------------------------------

/// Initialize all manager and handler instances.
///
/// * `base_dir` — base directory path (forwarded to `UploadHandler::new`).
/// * `rag_manager` — RAG manager instance (optional). In the live path this is
///   `None` (the application RAG singleton is disabled — see `rag_singleton`).
///
/// Returns the populated [`AppManagers`]. `MemoryManager::new` is the only
/// fallible constructor (`PyResult`), so the whole function is `PyResult`.
///
/// `async` because `MemoryVectorStore::new` probes its embedding endpoint and
/// the degraded-rebuild path `await`s `rebuild`.
pub async fn initialize_managers(
    base_dir: &str,
    rag_manager: Option<Arc<dyn RagManager>>,
) -> PyResult<AppManagers> {
    // Create directories first.
    create_directories();

    // Initialize core managers.
    let memory_manager = Arc::new(MemoryManager::new(&DATA_DIR)?);
    // `skills_manager = SkillsManager(DATA_DIR)` — the real disk-backed
    // SkillsManager (`crate::services::memory::skills`).
    let skills_manager =
        Arc::new(crate::services::memory::skills::SkillsManager::new(&DATA_DIR));
    let session_manager = Arc::new(SessionManager::new());
    // `set_session_manager(session_manager)` — enable Session.add_message()
    // persistence (the `core::models` trait-object global)...
    set_session_manager(session_manager.clone());
    // ...AND, on `unix` builds, the concrete `bg_monitor` global the background
    // monitor needs (see docstring). `bg_monitor` is `cfg(all(web, unix))`, so the
    // setter only exists there; non-`unix` web builds have no monitor and no
    // global to wire.
    #[cfg(unix)]
    crate::src::bg_monitor::set_session_manager(session_manager.clone());

    let upload_handler = Arc::new(UploadHandler::new(base_dir, &UPLOAD_DIR));
    let personal_docs_manager = Arc::new(PersonalDocsManager::new(&PERSONAL_DIR, rag_manager));
    let api_key_manager = Arc::new(APIKeyManager::new(&DATA_DIR));
    // The shared `preset_manager` instance — `Mutex`-wrapped so the routers that
    // mutate it (`&mut self` methods) can `.lock().await`, and shared (this one
    // `Arc`) across the preset + backup routers and `AppState`, like app.py.
    let preset_manager = Arc::new(tokio::sync::Mutex::new(PresetManager::new(&DATA_DIR)));
    // `ChatHandler` only needs a read-only `presets()` view (it never mutates the
    // manager), and its `Arc<dyn chat_handler::PresetManager>` seam returns a
    // borrow that a `tokio::sync::Mutex` can't yield synchronously. Hand it a
    // separate concrete `PresetManager` loaded from the SAME `presets.json` — the
    // registry is identical at startup, and ChatHandler reads only that initial
    // set (presets are rarely mutated at runtime; the routers own mutation).
    let chat_handler_presets = Arc::new(PresetManager::new(&DATA_DIR));

    // Initialize memory vector store (share embedding model with RAG if
    // available). DUCK-TYPE NOTE: Python passes
    // `getattr(rag_manager, '_model', None)`; the ported `RagManager` trait has
    // no `_model`, so we pass `None` (MemoryVectorStore builds its own lazy
    // embedding client) — the same end state as the Python `None` path.
    //
    // The whole block is Python's `try: ... except Exception as e:` — any
    // failure leaves `memory_vector = None`. The rebuild step (`&mut self`,
    // `async`) runs while the store is still owned, before the `Arc` wrap.
    let memory_vector: Option<Arc<MemoryVectorStore>> =
        match init_memory_vector(&memory_manager).await {
            Ok(mv) => mv,
            Err(e) => {
                // Python's `except Exception as e:` -> warn + None.
                logger::warning(&format!("MemoryVectorStore DEGRADED: {e}"));
                None
            }
        };

    // Initialize processors.
    //
    // `ChatProcessor(memory_manager, personal_docs_manager, memory_vector=...,
    // skills_manager=...)`. Python passes the live `memory_vector` straight
    // through, so chat-time semantic memory search runs whenever the store is
    // healthy (`_hybrid_retrieve`'s `has_vector` path). We wire it via
    // [`MemoryVectorSearchAdapter`] (bridging the store's `async` search into
    // ChatProcessor's sync `MemoryVectorSearch` trait). When DEGRADED the store is
    // already `None` here (Python sets `memory_vector = None`), so we pass `None`.
    //
    // The `RagSearch` collaborator stays `None` (COLLABORATOR DRIFT, see
    // docstring): no in-tree type implements `RagSearch` (the application RAG
    // singleton is disabled on the live path -> Python's `rag_manager` is
    // `None`). The `SkillsIndex` collaborator is now wired to the real
    // `SkillsManager` via [`SkillsIndexAdapter`].
    let memory_vector_search: Option<Arc<dyn MemoryVectorSearch>> =
        memory_vector.clone().map(|store| {
            Arc::new(MemoryVectorSearchAdapter { store }) as Arc<dyn MemoryVectorSearch>
        });
    let skills_index: Option<Arc<dyn SkillsIndex>> = Some(Arc::new(SkillsIndexAdapter {
        manager: skills_manager.clone(),
    }));
    let chat_processor = Arc::new(ChatProcessor::new(
        memory_manager.clone(),
        None, // personal_docs_manager.rag_manager as RagSearch (no bridge — see docstring)
        memory_vector_search, // memory_vector as MemoryVectorSearch (healthy store wired)
        skills_index, // skills_manager as SkillsIndex (real services.memory.skills SkillsManager)
    ));
    let research_handler = Arc::new(ResearchHandler::new());

    // Initialize chat handler with all dependencies.
    let chat_handler = Arc::new(ChatHandler::new(
        session_manager.clone(),
        memory_manager.clone(),
        Arc::new(CollaboratorStub), // chat_processor slot (stored, never called)
        Arc::new(CollaboratorStub), // research_handler slot (stored, never called)
        chat_handler_presets, // read-only `presets()` view (see note above)
        upload_handler.clone(),
    ));

    // Initialize model discovery.
    let model_discovery = Arc::new(ModelDiscovery::new(&DEFAULT_HOST, OPENAI_API_KEY.clone()));

    // Load and apply saved API keys.
    // `saved_keys = api_key_manager.load()`. Python lets a load failure surface;
    // the Rust `load` is `PyResult` — propagate to match the `Dict[str, Any]`
    // return contract.
    let saved_keys = api_key_manager.load()?;
    if let Some(brave) = saved_keys.get("brave") {
        // `update_search_config(api_key=saved_keys["brave"])` — wire the real
        // `crate::src::search::update_search_config`, which stashes the Brave API
        // key into the `SEARCH_CONFIG` global. `load()` decrypts to a JSON string.
        let brave_key = brave.as_str().unwrap_or("");
        crate::src::search::update_search_config(Some(brave_key));
        logger::info("Loaded Brave API key from saved configuration");
    }

    Ok(AppManagers {
        memory_manager,
        memory_vector,
        skills_manager,
        session_manager,
        upload_handler,
        personal_docs_manager,
        api_key_manager,
        preset_manager,
        chat_processor,
        research_handler,
        chat_handler,
        model_discovery,
    })
}

/// The `try:` body of Python's `memory_vector` block, factored out so the caller
/// can map any error (Python's `except Exception as e:`) to the `None` degrade.
///
/// Mirrors:
/// ```python
/// memory_vector = MemoryVectorStore(DATA_DIR, embedding_model=None)
/// if memory_vector.healthy:
///     if memory_vector.count() == 0:
///         existing = memory_manager.load()
///         if existing:
///             memory_vector.rebuild(existing)
///             logger.info(...)
///     logger.info("MemoryVectorStore initialized")
/// else:
///     logger.warning("MemoryVectorStore DEGRADED: ...")
///     memory_vector = None
/// ```
/// Returns `Ok(Some(store))` when healthy, `Ok(None)` when degraded
/// (`healthy == false`), and `Err(..)` when a rebuild fails (mapped to the
/// outer `except` -> `None` by the caller).
async fn init_memory_vector(
    memory_manager: &Arc<MemoryManager>,
) -> PyResult<Option<Arc<MemoryVectorStore>>> {
    // DUCK-TYPE NOTE: Python passes `getattr(rag_manager, '_model', None)`; the
    // ported `RagManager` trait has no `_model`, so we pass `None`
    // (MemoryVectorStore builds its own lazy embedding client) — the same end
    // state as the Python `None` path.
    let mut mv = MemoryVectorStore::new(&DATA_DIR, None).await;
    if !mv.healthy() {
        logger::warning("MemoryVectorStore DEGRADED: ChromaDB vector memory unavailable");
        return Ok(None);
    }
    // Rebuild index from existing memories if empty.
    if mv.count() == 0 {
        // `existing = memory_manager.load()` — owner=None (no arg).
        let existing = memory_manager.load(None);
        if !existing.is_empty() {
            // `memory_vector.rebuild(existing)` — `&mut self`; done while still
            // owned (before the Arc wrap). A failure propagates to the caller's
            // `except`-equivalent.
            mv.rebuild(&existing).await?;
            logger::info(&format!(
                "Rebuilt memory vector index from {} existing entries",
                existing.len()
            ));
        }
    }
    logger::info("MemoryVectorStore initialized");
    Ok(Some(Arc::new(mv)))
}
