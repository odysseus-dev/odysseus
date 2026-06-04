// src/config.rs  <- src/config.py
//! Application configuration structs.
//!
//! The Python module is built on `pydantic_settings.BaseSettings`, which has no
//! direct Rust analogue. Each settings class becomes a plain struct with a
//! `Default` impl that reproduces the pydantic field defaults, plus a
//! constructor that reads the per-class `env_prefix`-scoped environment
//! variables via [`crate::pyos::getenv`] (mirroring pydantic's automatic
//! `<PREFIX><FIELD>` env reads).
//!
//! PARITY NOTES / DOCUMENTED DRIFT:
//! - pydantic's `env_prefix` machinery is replaced by explicit `getenv` reads.
//!   No code in the tree sets the `DATA_*` / `LLM_*` / `SEARCH_*` / `SECURITY_*`
//!   env vars, so the observable behaviour collapses to the field defaults.
//! - `base_dir` is derived from [`crate::core::constants::BASE_DIR`], which is the
//!   repo root — the same directory Python's `Path(__file__).parent.parent`
//!   resolves to. The `AppConfig.set_data_paths` `field_validator` that rebases
//!   the data paths under `base_dir` is reproduced in [`AppConfig::default`].
//! - Python executes `validate_config()` at *import time* (the last line of the
//!   module). Rust has no import-time execution, and this module is vestigial
//!   (no in-batch consumer wires it), so [`validate_config`] is NOT run on load —
//!   it is exposed as a free function for parity but never auto-invoked.
//! - Pydantic `Path` fields become owned `String`s (joined with the platform
//!   separator via `crate::pyos::path::join`), matching how the rest of the
//!   crate carries filesystem paths.

use crate::core::constants;
use crate::pyos as os;
use once_cell::sync::Lazy;

/// `int(os.getenv(name, default))` with the Python `int()`-on-failure-raises
/// behaviour softened to "fall back to the default" (config has no caller that
/// depends on a malformed env var raising).
fn env_int(key: &str, default: i64) -> i64 {
    match os::getenv_opt(key) {
        Some(v) => v.trim().parse::<i64>().unwrap_or(default),
        None => default,
    }
}

/// `float(os.getenv(name, default))` (same soft-fallback as [`env_int`]).
fn env_float(key: &str, default: f64) -> f64 {
    match os::getenv_opt(key) {
        Some(v) => v.trim().parse::<f64>().unwrap_or(default),
        None => default,
    }
}

/// Configuration for data storage and file handling (`DataConfig`, env_prefix `DATA_`).
#[derive(Debug, Clone)]
pub struct DataConfig {
    /// Base directory for the application.
    pub base_dir: String,
    /// Main data directory.
    pub data_dir: String,
    /// Directory for uploaded files.
    pub uploads_dir: String,
    /// Sessions storage file.
    pub sessions_file: String,
    /// Memory storage file.
    pub memory_file: String,
    /// Memory document file.
    pub memory_doc: String,
    /// Personal documents directory.
    pub personal_dir: String,
    /// Runbook directory.
    pub runbook_dir: String,
    /// Maximum upload size in bytes (10MB).
    pub max_upload_size: i64,
    /// Allowed file extensions for uploads.
    pub allowed_extensions: Vec<String>,
    /// Chunk size for document processing.
    pub chunk_size: i64,
    /// Overlap between chunks for document processing.
    pub chunk_overlap: i64,
    /// Number of days after which to clean up old uploads.
    pub cleanup_days: i64,
}

impl Default for DataConfig {
    fn default() -> Self {
        // base_dir = Path(__file__).parent.parent (the repo root).
        let base_dir = constants::BASE_DIR.trim_end_matches('/').to_string();
        // The pydantic `set_data_paths` validator rebases every path under
        // `base_dir / "data"`. We reproduce that derivation here so the struct's
        // default already carries the rebased absolute paths.
        let data_dir = os::path::join(&base_dir, "data");
        DataConfig {
            uploads_dir: os::path::join(&data_dir, "uploads"),
            sessions_file: os::path::join(&data_dir, "sessions.json"),
            memory_file: os::path::join(&data_dir, "memory.json"),
            memory_doc: os::path::join(&data_dir, "memory_doc.md"),
            personal_dir: os::path::join(&data_dir, "personal_docs"),
            runbook_dir: os::path::join(&os::path::join(&data_dir, "personal_docs"), "runbook"),
            max_upload_size: 10 * 1024 * 1024,
            allowed_extensions: vec![
                ".txt", ".py", ".html", ".md", ".json", ".csv", ".png", ".jpg", ".jpeg", ".gif",
                ".bmp", ".webp", ".tiff", ".pdf",
            ]
            .into_iter()
            .map(String::from)
            .collect(),
            chunk_size: 1000,
            chunk_overlap: 200,
            cleanup_days: 30,
            base_dir,
            data_dir,
        }
    }
}

impl DataConfig {
    /// Build from defaults overlaid with the `DATA_*` environment variables that
    /// pydantic's `env_prefix="DATA_"` would have read.
    pub fn from_env() -> Self {
        let mut c = DataConfig::default();
        if let Some(v) = os::getenv_opt("DATA_BASE_DIR") {
            c.base_dir = v;
        }
        if let Some(v) = os::getenv_opt("DATA_DATA_DIR") {
            c.data_dir = v;
        }
        c.max_upload_size = env_int("DATA_MAX_UPLOAD_SIZE", c.max_upload_size);
        c.chunk_size = env_int("DATA_CHUNK_SIZE", c.chunk_size);
        c.chunk_overlap = env_int("DATA_CHUNK_OVERLAP", c.chunk_overlap);
        c.cleanup_days = env_int("DATA_CLEANUP_DAYS", c.cleanup_days);
        c
    }
}

/// Configuration for LLM integration (`LLMConfig`, env_prefix `LLM_`).
#[derive(Debug, Clone)]
pub struct LLMConfig {
    /// Default host for LLM services.
    pub default_host: String,
    /// OpenAI API key if using OpenAI.
    pub openai_api_key: Option<String>,
    /// OpenAI compatible API path.
    pub openai_compat_path: String,
    /// Maximum number of context messages to keep.
    pub max_context_messages: i64,
    /// Request timeout in seconds.
    pub request_timeout: i64,
    /// LLM streaming timeout in seconds.
    pub llm_stream_timeout: i64,
    /// Maximum tokens for LLM responses.
    pub llm_max_tokens: i64,
    /// Temperature for LLM responses.
    pub llm_temperature: f64,
}

impl Default for LLMConfig {
    fn default() -> Self {
        LLMConfig {
            default_host: "localhost".to_string(),
            openai_api_key: None,
            openai_compat_path: "/v1/chat/completions".to_string(),
            max_context_messages: 90,
            request_timeout: 20,
            llm_stream_timeout: 30,
            llm_max_tokens: 4096,
            llm_temperature: 0.3,
        }
    }
}

impl LLMConfig {
    /// Build from defaults overlaid with the `LLM_*` environment variables.
    pub fn from_env() -> Self {
        let mut c = LLMConfig::default();
        c.default_host = os::getenv("LLM_DEFAULT_HOST", &c.default_host);
        c.openai_api_key = os::getenv_opt("LLM_OPENAI_API_KEY").or(c.openai_api_key);
        c.openai_compat_path = os::getenv("LLM_OPENAI_COMPAT_PATH", &c.openai_compat_path);
        c.max_context_messages = env_int("LLM_MAX_CONTEXT_MESSAGES", c.max_context_messages);
        c.request_timeout = env_int("LLM_REQUEST_TIMEOUT", c.request_timeout);
        c.llm_stream_timeout = env_int("LLM_LLM_STREAM_TIMEOUT", c.llm_stream_timeout);
        c.llm_max_tokens = env_int("LLM_LLM_MAX_TOKENS", c.llm_max_tokens);
        c.llm_temperature = env_float("LLM_LLM_TEMPERATURE", c.llm_temperature);
        c
    }
}

/// Configuration for search functionality (`SearchConfig`, env_prefix `SEARCH_`).
#[derive(Debug, Clone)]
pub struct SearchConfig {
    /// SearXNG instance URL (self-hosted).
    pub searxng_instance: String,
    /// Number of search results to retrieve.
    pub web_search_count: i64,
    /// Maximum number of pages to search.
    pub web_search_max_pages: i64,
    /// Maximum number of worker threads for web search.
    pub web_search_max_workers: i64,
    /// URL for research service.
    pub research_service_url: String,
    /// Research service timeout in seconds.
    pub research_timeout: i64,
    /// SerpAPI key if used.
    pub serpapi_key: Option<String>,
    /// Google API key if used.
    pub google_api_key: Option<String>,
    /// Google Custom Search Engine ID if used.
    pub google_cx: Option<String>,
}

impl Default for SearchConfig {
    fn default() -> Self {
        SearchConfig {
            searxng_instance: "http://localhost:8080".to_string(),
            web_search_count: 10,
            web_search_max_pages: 6,
            web_search_max_workers: 4,
            research_service_url: "http://localhost:8003/research".to_string(),
            research_timeout: 300,
            serpapi_key: None,
            google_api_key: None,
            google_cx: None,
        }
    }
}

impl SearchConfig {
    /// Build from defaults overlaid with the `SEARCH_*` environment variables.
    pub fn from_env() -> Self {
        let mut c = SearchConfig::default();
        c.searxng_instance = os::getenv("SEARCH_SEARXNG_INSTANCE", &c.searxng_instance);
        c.web_search_count = env_int("SEARCH_WEB_SEARCH_COUNT", c.web_search_count);
        c.web_search_max_pages = env_int("SEARCH_WEB_SEARCH_MAX_PAGES", c.web_search_max_pages);
        c.web_search_max_workers =
            env_int("SEARCH_WEB_SEARCH_MAX_WORKERS", c.web_search_max_workers);
        c.research_service_url = os::getenv("SEARCH_RESEARCH_SERVICE_URL", &c.research_service_url);
        c.research_timeout = env_int("SEARCH_RESEARCH_TIMEOUT", c.research_timeout);
        c.serpapi_key = os::getenv_opt("SEARCH_SERPAPI_KEY").or(c.serpapi_key);
        c.google_api_key = os::getenv_opt("SEARCH_GOOGLE_API_KEY").or(c.google_api_key);
        c.google_cx = os::getenv_opt("SEARCH_GOOGLE_CX").or(c.google_cx);
        c
    }
}

/// Configuration for security and rate limiting (`SecurityConfig`, env_prefix `SECURITY_`).
#[derive(Debug, Clone)]
pub struct SecurityConfig {
    /// Maximum concurrent uploads per IP.
    pub max_concurrent_uploads: i64,
    /// Maximum uploads per minute per IP.
    pub upload_rate_limit: i64,
    /// Rate limit window in seconds.
    pub upload_rate_window: i64,
    /// Maximum number of rate limit entries to keep.
    pub upload_rate_max_entries: i64,
    /// Allowed origins for CORS.
    pub allowed_origins: Vec<String>,
    /// Maximum file size in bytes.
    pub max_file_size: i64,
    /// Potentially dangerous MIME types to block.
    pub dangerous_file_types: Vec<String>,
    /// Potentially dangerous file extensions to block.
    pub dangerous_extensions: Vec<String>,
}

impl Default for SecurityConfig {
    fn default() -> Self {
        SecurityConfig {
            max_concurrent_uploads: 3,
            upload_rate_limit: 5,
            upload_rate_window: 60,
            upload_rate_max_entries: 1000,
            allowed_origins: vec!["*".to_string()],
            max_file_size: 10 * 1024 * 1024,
            dangerous_file_types: vec![
                "application/x-executable",
                "application/x-sharedlib",
                "application/x-dll",
                "application/x-msdownload",
                "application/x-sh",
                "application/x-bat",
                "application/x-vbs",
                "application/javascript",
                "application/x-javascript",
            ]
            .into_iter()
            .map(String::from)
            .collect(),
            dangerous_extensions: vec![
                ".exe", ".dll", ".bat", ".cmd", ".sh", ".bash", ".js", ".vbs", ".ps1", ".py",
                ".php", ".jsp", ".asp", ".aspx",
            ]
            .into_iter()
            .map(String::from)
            .collect(),
        }
    }
}

impl SecurityConfig {
    /// Build from defaults overlaid with the `SECURITY_*` environment variables.
    pub fn from_env() -> Self {
        let mut c = SecurityConfig::default();
        c.max_concurrent_uploads = env_int("SECURITY_MAX_CONCURRENT_UPLOADS", c.max_concurrent_uploads);
        c.upload_rate_limit = env_int("SECURITY_UPLOAD_RATE_LIMIT", c.upload_rate_limit);
        c.upload_rate_window = env_int("SECURITY_UPLOAD_RATE_WINDOW", c.upload_rate_window);
        c.upload_rate_max_entries =
            env_int("SECURITY_UPLOAD_RATE_MAX_ENTRIES", c.upload_rate_max_entries);
        c.max_file_size = env_int("SECURITY_MAX_FILE_SIZE", c.max_file_size);
        c
    }
}

/// Main application configuration combining all components (`AppConfig`).
#[derive(Debug, Clone)]
pub struct AppConfig {
    /// Data storage configuration.
    pub data: DataConfig,
    /// LLM integration configuration.
    pub llm: LLMConfig,
    /// Search configuration.
    pub search: SearchConfig,
    /// Security configuration.
    pub security: SecurityConfig,
    /// Enable debug mode.
    pub debug: bool,
    /// Logging level.
    pub log_level: String,
}

impl Default for AppConfig {
    fn default() -> Self {
        // Mirrors `AppConfig`'s class-level field defaults: each sub-config is
        // its own default instance. The `set_data_paths` validator's path
        // rebasing is folded into `DataConfig::default`.
        AppConfig {
            data: DataConfig::default(),
            llm: LLMConfig::default(),
            search: SearchConfig::default(),
            security: SecurityConfig::default(),
            debug: false,
            log_level: "INFO".to_string(),
        }
    }
}

/// Global config instance (`config = AppConfig()`).
///
/// Built from the env-aware constructors so the singleton reflects any
/// `DATA_*` / `LLM_*` / `SEARCH_*` / `SECURITY_*` overrides present at first
/// access (pydantic reads env at instantiation).
pub static CONFIG: Lazy<AppConfig> = Lazy::new(|| AppConfig {
    data: DataConfig::from_env(),
    llm: LLMConfig::from_env(),
    search: SearchConfig::from_env(),
    security: SecurityConfig::from_env(),
    debug: matches!(
        os::getenv("DEBUG", "False").to_lowercase().as_str(),
        "1" | "true" | "yes"
    ),
    log_level: os::getenv("LOG_LEVEL", "INFO"),
});

/// Create required directories if they don't exist.
///
/// Mirrors `create_directories`: `data_dir`, `uploads_dir`, `personal_dir`,
/// `runbook_dir` are created (with parents) on the global config.
pub fn create_directories() {
    let directories = [
        &CONFIG.data.data_dir,
        &CONFIG.data.uploads_dir,
        &CONFIG.data.personal_dir,
        &CONFIG.data.runbook_dir,
    ];
    for directory in directories {
        // mkdir(parents=True, exist_ok=True)
        let _ = os::makedirs(directory, true);
    }
}

/// Validate the application configuration.
///
/// Mirrors `validate_config`: the two Python checks are no-ops (a `192.168.`
/// host is assumed valid; a missing OpenAI key is fine), and the function ends
/// by creating the directories.
///
/// NOTE: unlike Python, this is NOT executed at module load — Rust has no
/// import-time side effects. Call it explicitly if directory creation is wanted.
pub fn validate_config() {
    // Check if LLM host is reachable if specified (local IP -> assume valid).
    if !CONFIG.llm.default_host.is_empty() && CONFIG.llm.default_host.starts_with("192.168.") {
        // local IP, assume valid
    }
    // Check if API keys are set when needed (OpenAI key optional -> OK).
    if CONFIG.llm.openai_api_key.is_none() {
        // not using OpenAI is fine
    }
    create_directories();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_match_python() {
        let c = AppConfig::default();
        assert_eq!(c.data.max_upload_size, 10 * 1024 * 1024);
        assert_eq!(c.data.chunk_size, 1000);
        assert_eq!(c.data.chunk_overlap, 200);
        assert_eq!(c.data.cleanup_days, 30);
        assert_eq!(c.data.allowed_extensions.len(), 14);
        assert_eq!(c.llm.default_host, "localhost");
        assert_eq!(c.llm.openai_compat_path, "/v1/chat/completions");
        assert_eq!(c.llm.max_context_messages, 90);
        assert_eq!(c.llm.request_timeout, 20);
        assert_eq!(c.llm.llm_max_tokens, 4096);
        assert!((c.llm.llm_temperature - 0.3).abs() < 1e-9);
        assert_eq!(c.search.searxng_instance, "http://localhost:8080");
        assert_eq!(c.search.web_search_count, 10);
        assert_eq!(c.search.research_service_url, "http://localhost:8003/research");
        assert_eq!(c.security.max_concurrent_uploads, 3);
        assert_eq!(c.security.allowed_origins, vec!["*".to_string()]);
        assert_eq!(c.security.dangerous_extensions.len(), 14);
        assert!(!c.debug);
        assert_eq!(c.log_level, "INFO");
    }

    #[test]
    fn data_paths_rebased_under_base_dir() {
        let c = DataConfig::default();
        // data_dir is base_dir/data; the rest hang off data_dir.
        assert!(c.data_dir.ends_with("/data") || c.data_dir.ends_with("\\data"));
        assert!(c.uploads_dir.ends_with("uploads"));
        assert!(c.sessions_file.ends_with("sessions.json"));
        assert!(c.runbook_dir.ends_with("runbook"));
        assert!(c.personal_dir.ends_with("personal_docs"));
    }
}
