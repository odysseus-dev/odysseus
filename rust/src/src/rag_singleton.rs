// src/rag_singleton.rs  <- src/rag_singleton.py
//! RAG singleton instance for the application.
//!
//! ## Port classification — PORT_VIA_DB (web)
//!
//! `get_rag_manager()` is **disabled** in Python (it unconditionally returns
//! `None`) and this is the live, faithful behaviour we port: vector document RAG
//! (`VectorRAG`/ChromaDB) is unused, and re-attempting a broken init every 30s
//! caused hangs, so personal-doc routes fall back to non-vector behaviour. The Rust
//! `get_rag_manager()` therefore returns `None`.
//!
//! `_get_rag_manager_legacy()` is the original lazy initializer the Python keeps
//! "for reference / easy re-enable". It is ported faithfully but, like the Python,
//! is **never called** — so it carries `#[allow(dead_code)]`. It reproduces the
//! module globals (`rag_instance`, `_last_attempt`) as `once_cell::Lazy<Mutex<…>>`,
//! the 30s retry throttle via `std::time::Instant`, the
//! `BASE_DIR/data/rag` persist directory, and the `.healthy` gate. The `ImportError`
//! branch is dead in Rust (no dynamic import to fail), so the legacy fn's only
//! failure path is the `VectorRAG` init leaving the instance unhealthy.
//!
//! Feature gate: `web` (the legacy initializer constructs a `VectorRAG`).

use std::sync::{Arc, Mutex};
use std::time::Instant;

use once_cell::sync::Lazy;

use crate::pylog as logger;
use crate::src::constants::BASE_DIR;
use crate::src::rag_vector::VectorRAG;

/// `rag_instance = None` — the cached singleton, behind a `Mutex` (Python module
/// global). `Arc<VectorRAG>` so a clone can be handed back without giving up the
/// cache. Only touched by `_get_rag_manager_legacy`.
#[allow(dead_code)]
static RAG_INSTANCE: Lazy<Mutex<Option<Arc<VectorRAG>>>> = Lazy::new(|| Mutex::new(None));

/// `_last_attempt = 0.0` — the monotonic timestamp of the last init attempt.
/// Modeled as an `Option<Instant>` (`None` == the Python `0.0` "never attempted, so
/// the first call is always due").
#[allow(dead_code)]
static LAST_ATTEMPT: Lazy<Mutex<Option<Instant>>> = Lazy::new(|| Mutex::new(None));

/// `_RETRY_INTERVAL = 30` — seconds between re-init attempts.
#[allow(dead_code)]
const RETRY_INTERVAL: u64 = 30;

/// `get_rag_manager()`.
///
/// Disabled: vector document RAG (`VectorRAG`/ChromaDB) is unused and its client is
/// incompatible with the installed pydantic. Returns `None` so personal-doc routes
/// fall back to non-vector behaviour instead of re-attempting (and re-hanging on) a
/// broken ChromaDB init every 30s.
pub fn get_rag_manager() -> Option<Arc<VectorRAG>> {
    None
}

/// `_get_rag_manager_legacy()` — original lazy initializer, kept for reference /
/// easy re-enable.
///
/// Never called (matching the Python, where `get_rag_manager` shadows it). Async
/// because the ported `VectorRAG` constructor is async (HTTP dimension probe).
#[allow(dead_code)]
async fn get_rag_manager_legacy() -> Option<Arc<VectorRAG>> {
    // if rag_instance is not None: return rag_instance
    {
        let guard = RAG_INSTANCE.lock().unwrap();
        if let Some(instance) = guard.as_ref() {
            return Some(instance.clone());
        }
    }

    // now = time.monotonic()
    // if now - _last_attempt < _RETRY_INTERVAL: return None  # too soon to retry
    {
        let last = LAST_ATTEMPT.lock().unwrap();
        if let Some(prev) = *last {
            if prev.elapsed().as_secs() < RETRY_INTERVAL {
                return None;
            }
        }
    }

    // _last_attempt = now
    *LAST_ATTEMPT.lock().unwrap() = Some(Instant::now());

    // base_dir = Path(__file__).parent.parent
    // persist_dir = os.path.join(base_dir, "data", "rag")
    let persist_dir = crate::pyos::path::join(&crate::pyos::path::join(&BASE_DIR, "data"), "rag");

    // rag_instance = VectorRAG(persist_directory=persist_dir)
    // The Python `ImportError` branch (dynamic `from src.rag_vector import …`) has
    // no Rust analogue; the only failure mode is `VectorRAG` init leaving the
    // instance unhealthy, which the `.healthy` gate below catches.
    let instance = VectorRAG::new(&persist_dir).await;

    // if not rag_instance.healthy: warn + None; else: cache + info
    if !instance.healthy() {
        logger::warning("VectorRAG created but not healthy, will retry later");
        None
    } else {
        logger::info("Initialized VectorRAG with ChromaDB");
        let arc = Arc::new(instance);
        *RAG_INSTANCE.lock().unwrap() = Some(arc.clone());
        Some(arc)
    }
}
