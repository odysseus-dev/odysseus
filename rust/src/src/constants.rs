// src/constants.rs  <- src/constants.py
//! Application-wide constants and configuration values.

use crate::pyos as os;
use once_cell::sync::Lazy;

pub const APP_VERSION: &str = "1.0.0";

// Base paths (see core::constants for the BASE_DIR derivation note).
pub static BASE_DIR: Lazy<String> = Lazy::new(|| {
    let manifest = env!("CARGO_MANIFEST_DIR");
    let root = std::path::Path::new(manifest)
        .parent()
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| std::path::PathBuf::from(manifest));
    format!("{}/", root.to_string_lossy())
});
pub static STATIC_DIR: Lazy<String> = Lazy::new(|| os::path::join(&BASE_DIR, "static"));
pub static DATA_DIR: Lazy<String> = Lazy::new(|| os::path::join(&BASE_DIR, "data"));

// Data file paths
pub static SESSIONS_FILE: Lazy<String> = Lazy::new(|| os::path::join(&DATA_DIR, "sessions.json"));
pub static MEMORY_FILE: Lazy<String> = Lazy::new(|| os::path::join(&DATA_DIR, "memory.json"));
pub static MEMORY_DOC: Lazy<String> = Lazy::new(|| os::path::join(&DATA_DIR, "memory_doc.md"));
pub static PERSONAL_DIR: Lazy<String> = Lazy::new(|| os::path::join(&DATA_DIR, "personal_docs"));
pub static RUNBOOK_DIR: Lazy<String> = Lazy::new(|| os::path::join(&PERSONAL_DIR, "runbook"));
pub static UPLOAD_DIR: Lazy<String> = Lazy::new(|| os::path::join(&DATA_DIR, "uploads"));
pub static FEATURES_FILE: Lazy<String> = Lazy::new(|| os::path::join(&DATA_DIR, "features.json"));
pub static SETTINGS_FILE: Lazy<String> = Lazy::new(|| os::path::join(&DATA_DIR, "settings.json"));

// API Configuration
pub const MAX_CONTEXT_MESSAGES: i64 = 90;
pub const REQUEST_TIMEOUT: i64 = 20;
pub const OPENAI_COMPAT_PATH: &str = "/v1/chat/completions";

// Environment variables with defaults
pub static DEFAULT_HOST: Lazy<String> = Lazy::new(|| os::getenv("LLM_HOST", "localhost"));
pub static LLM_HOSTS: Lazy<Vec<String>> = Lazy::new(|| {
    os::getenv("LLM_HOSTS", "")
        .split(',')
        .map(|h| h.trim().to_string())
        .filter(|h| !h.is_empty())
        .collect()
});
pub static OPENAI_API_KEY: Lazy<Option<String>> = Lazy::new(|| os::getenv_opt("OPENAI_API_KEY"));
pub static SEARXNG_INSTANCE: Lazy<String> =
    Lazy::new(|| os::getenv("SEARXNG_INSTANCE", "http://localhost:8080"));

// Cleanup configuration
pub static CLEANUP_ENABLED: Lazy<bool> =
    Lazy::new(|| os::getenv("CLEANUP_ENABLED", "True").to_lowercase() == "true");
pub static CLEANUP_INTERVAL_HOURS: Lazy<i64> =
    Lazy::new(|| crate::pybuiltins::int(&os::getenv("CLEANUP_INTERVAL_HOURS", "24")));

// Default parameters
pub const DEFAULT_TEMPERATURE: f64 = 1.0;
pub const DEFAULT_MAX_TOKENS: i64 = 0;
