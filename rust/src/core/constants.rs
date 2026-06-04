// core/constants.rs  <- core/constants.py
//! Application-wide constants and configuration values.

use crate::pyos as os;
use once_cell::sync::Lazy;

pub const APP_VERSION: &str = "0.9.1";

// Base paths.
//
// The Python computes `BASE_DIR = dirname(dirname(abspath(__file__))) + "/"`,
// which from `core/constants.py` resolves to the repository root. The Rust
// crate lives one level deeper (under `rust/`), so the faithful analogue is the
// parent of the crate manifest directory — i.e. the same repository root, so the
// rewrite reads/writes the very same `data/` tree as the live Python app.
pub static BASE_DIR: Lazy<String> = Lazy::new(|| {
    let manifest = env!("CARGO_MANIFEST_DIR"); // .../<repo>/rust
    let root = std::path::Path::new(manifest)
        .parent()
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| std::path::PathBuf::from(manifest));
    format!("{}/", root.to_string_lossy())
});
pub static STATIC_DIR: Lazy<String> = Lazy::new(|| os::path::join(&BASE_DIR, "static"));
// `DATA_DIR` is `<repo>/data` by default. `ODYSSEUS_DATA_DIR` overrides it so the
// runnable server can point ALL of auth.json / the DB / tool-security (which
// constructs its own `AuthManager::new()`) at one isolated dir consistently. The
// Python has no such override (its BASE_DIR is fixed from `__file__`), so this is
// run-only "surrounding code"; default behavior is unchanged when unset.
pub static DATA_DIR: Lazy<String> = Lazy::new(|| match os::getenv_opt("ODYSSEUS_DATA_DIR") {
    Some(d) if !d.is_empty() => d,
    _ => os::path::join(&BASE_DIR, "data"),
});

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
