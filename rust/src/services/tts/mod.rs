// services/tts/mod.rs  <- services/tts/__init__.py
//! TTS service — text-to-speech.
//!
//! Mirrors `services/tts/__init__.py`:
//! ```python
//! from .tts_service import (TTSService, get_tts_service)
//! __all__ = ["TTSService", "get_tts_service"]
//! ```

pub mod tts_service;

pub use tts_service::{get_tts_service, TTSService};
