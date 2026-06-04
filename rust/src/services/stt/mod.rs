// services/stt/mod.rs  <- services/stt/__init__.py
//! STT service — speech-to-text.
//!
//! Mirrors `services/stt/__init__.py`:
//! ```python
//! from services.stt.stt_service import get_stt_service
//! __all__ = ["get_stt_service"]
//! ```
//!
//! `__all__` only exports `get_stt_service`; `STTService` itself stays public
//! (callers reference the type directly), so it is re-exported too.

pub mod stt_service;

pub use stt_service::{get_stt_service, STTService};
