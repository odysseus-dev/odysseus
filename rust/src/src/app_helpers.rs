// src/app_helpers.rs  <- src/app_helpers.py
//! Small filesystem / encoding helpers used across the app.

use crate::pyos as os;
use base64::Engine as _;
use std::ffi::OsString;
use std::path::{Component, Path, PathBuf};

/// Read file if it exists, return empty string otherwise.
pub fn read_if_exists(path: &str) -> String {
    // try: with open(path, "r", encoding="utf-8") as f: return f.read().strip()
    // except Exception: return ""
    match std::fs::read_to_string(path) {
        // Python text mode performs universal-newline translation: every
        // "\r\n" and bare "\r" becomes "\n" before .strip(). Rust's
        // read_to_string returns raw bytes, so normalize to match.
        Ok(s) => s.replace("\r\n", "\n").replace('\r', "\n").trim().to_string(),
        Err(_) => String::new(),
    }
}

/// Convert file to data URL.
///
/// Mirrors `open(path, "rb")` then base64-encoding the bytes; if the file
/// cannot be read the underlying `OSError` is propagated (Python would raise).
pub fn file_to_data_url(path: &str, mime: &str) -> crate::error::PyResult<String> {
    // with open(path, "rb") as f: b64 = base64.b64encode(f.read()).decode("ascii")
    let data = std::fs::read(path)?;
    let b64 = base64::engine::general_purpose::STANDARD.encode(&data);
    // return f"data:{mime};base64,{b64}"
    Ok(format!("data:{mime};base64,{b64}"))
}

/// Join paths and return absolute path.
pub fn abs_join(base_dir: &str, rel: &str) -> String {
    // return os.path.abspath(os.path.join(base_dir, rel))
    abspath(&os::path::join(base_dir, rel))
}

/// Check if path is inside base directory.
pub fn inside_base_dir(base_dir: &str, path: &str) -> bool {
    // base = os.path.realpath(base_dir)
    // p = os.path.realpath(path)
    let base = realpath(base_dir);
    let p = realpath(path);
    // try: return os.path.commonpath([base, p]) == base
    // except Exception: return False
    match commonpath(&[&base, &p]) {
        Some(common) => common == base,
        None => false,
    }
}

// --- helpers mirroring os.path semantics ------------------------------------

/// `os.path.abspath`: normalize and make absolute against the current working
/// directory, without touching the filesystem (no symlink resolution).
fn abspath(path: &str) -> String {
    let p = Path::new(path);
    let joined: PathBuf = if p.is_absolute() {
        p.to_path_buf()
    } else {
        let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
        cwd.join(p)
    };
    normpath(&joined)
}

/// `os.path.realpath`: resolve symlinks on the longest *existing* ancestor of
/// the path and lexically re-append the nonexistent tail, mirroring CPython.
///
/// `std::fs::canonicalize` alone errors on any nonexistent component, which
/// would silently drop us to a non-symlink-resolving `abspath` — wrong on
/// systems where parents are symlinks (macOS `/tmp`, `/var`, `/etc`). Since
/// `inside_base_dir` is a security boundary, the two operands must resolve
/// against the same root, so we resolve the existing prefix explicitly.
pub(crate) fn realpath(path: &str) -> String {
    let abs = abspath(path); // absolute + lexically normalized (no `.`/`..`)
    let mut prefix = PathBuf::from(&abs);
    // Components stripped off the (nonexistent) tail, deepest first.
    let mut tail: Vec<OsString> = Vec::new();
    loop {
        match std::fs::canonicalize(&prefix) {
            Ok(mut real) => {
                // Re-append the tail in original (top-down) order.
                for comp in tail.iter().rev() {
                    real.push(comp);
                }
                return normpath(&real);
            }
            Err(_) => match prefix.file_name() {
                Some(name) => {
                    tail.push(name.to_os_string());
                    if !prefix.pop() {
                        break;
                    }
                }
                None => break,
            },
        }
    }
    // Even the root could not be canonicalized — fall back to the lexical form.
    abs
}

/// Lexical path normalization (collapse `.`/`..`, dedup separators), matching
/// `os.path.normpath` closely enough for the comparisons used here.
fn normpath(path: &Path) -> String {
    let mut out: Vec<Component> = Vec::new();
    for comp in path.components() {
        match comp {
            Component::CurDir => {}
            Component::ParentDir => {
                match out.last() {
                    Some(Component::Normal(_)) => {
                        out.pop();
                    }
                    Some(Component::RootDir) | Some(Component::Prefix(_)) => {}
                    _ => out.push(comp),
                }
            }
            other => out.push(other),
        }
    }
    let mut result = PathBuf::new();
    for comp in out {
        result.push(comp.as_os_str());
    }
    let s = result.to_string_lossy().into_owned();
    if s.is_empty() {
        ".".to_string()
    } else {
        s
    }
}

/// `os.path.commonpath`: longest common sub-path. Returns `None` (the Python
/// `ValueError`) when the inputs mix absolute and relative paths.
pub(crate) fn commonpath(paths: &[&str]) -> Option<String> {
    if paths.is_empty() {
        return None;
    }
    let split = |p: &str| -> Vec<String> {
        Path::new(p)
            .components()
            .map(|c| c.as_os_str().to_string_lossy().into_owned())
            .collect()
    };
    let first_abs = Path::new(paths[0]).is_absolute();
    for p in paths {
        if Path::new(p).is_absolute() != first_abs {
            return None; // raise ValueError: Can't mix absolute and relative paths
        }
    }
    let split_paths: Vec<Vec<String>> = paths.iter().map(|p| split(p)).collect();
    let min_len = split_paths.iter().map(|v| v.len()).min().unwrap_or(0);
    let mut common: Vec<String> = Vec::new();
    for i in 0..min_len {
        let seg = &split_paths[0][i];
        if split_paths.iter().all(|v| &v[i] == seg) {
            common.push(seg.clone());
        } else {
            break;
        }
    }
    let mut result = PathBuf::new();
    for seg in &common {
        result.push(seg);
    }
    Some(result.to_string_lossy().into_owned())
}
