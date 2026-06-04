//! `pyos` — a thin shim over the parts of Python's `os` / `os.path` standard
//! library that the translated modules use. This is *support* code that exists
//! purely so the rewrite can read line-by-line like the Python original
//! (`os.path.join(a, b)`, `os.getenv(k, default)`, `os.replace(tmp, path)`, …).
//!
//! It is deliberately minimal: only the surface the foundation actually calls.

/// `os.path` helpers.
pub mod path {
    use std::path::Path;

    /// `os.path.join(a, b)`.
    pub fn join(a: &str, b: &str) -> String {
        Path::new(a).join(b).to_string_lossy().into_owned()
    }

    /// `os.path.exists(p)`.
    pub fn exists(p: &str) -> bool {
        Path::new(p).exists()
    }

    /// `os.path.dirname(p)` — everything up to (but excluding) the final
    /// separator, or `""` when there is none (matching CPython).
    pub fn dirname(p: &str) -> String {
        match p.rfind('/') {
            Some(idx) => p[..idx].to_string(),
            None => String::new(),
        }
    }

    /// `os.path.basename(p)` — the component after the final separator.
    pub fn basename(p: &str) -> String {
        match p.rfind('/') {
            Some(idx) => p[idx + 1..].to_string(),
            None => p.to_string(),
        }
    }
}

/// `os.getenv(key, default)` — returns `default` when the variable is unset.
pub fn getenv(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.to_string())
}

/// `os.getenv(key)` — `None` when unset (the single-argument form).
pub fn getenv_opt(key: &str) -> Option<String> {
    std::env::var(key).ok()
}

/// `os.environ[key] = value` — set a process environment variable.
///
/// `embeddings::get_embedding_client` writes `EMBEDDING_URL` / `EMBEDDING_MODEL`
/// back into the process env (so other code that re-reads `os.getenv` sees the
/// persisted custom endpoint), exactly as the Python's
/// `os.environ["EMBEDDING_URL"] = url` does. `std::env::set_var` is safe on the
/// 2021 edition this crate targets.
pub fn set_var(key: &str, value: &str) {
    std::env::set_var(key, value);
}

/// `os.getpid()`.
pub fn getpid() -> u32 {
    std::process::id()
}

/// `os.makedirs(path, exist_ok=...)`.
pub fn makedirs(path: &str, exist_ok: bool) -> std::io::Result<()> {
    if path.is_empty() {
        return Ok(());
    }
    match std::fs::create_dir_all(path) {
        Ok(()) => Ok(()),
        Err(e) if exist_ok && e.kind() == std::io::ErrorKind::AlreadyExists => Ok(()),
        Err(e) => Err(e),
    }
}

/// `os.replace(src, dst)` — atomic rename on the same filesystem (POSIX).
pub fn replace(src: &str, dst: &str) -> std::io::Result<()> {
    std::fs::rename(src, dst)
}

/// `os.chmod(path, mode)`.
#[cfg(unix)]
pub fn chmod(path: &str, mode: u32) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode))
}

#[cfg(not(unix))]
pub fn chmod(_path: &str, _mode: u32) -> std::io::Result<()> {
    Ok(())
}
