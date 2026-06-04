// src/search/cache.rs  <- src/search/cache.py
//! Search and content caching with LRU eviction.

use crate::src::constants::BASE_DIR;
use chrono::{DateTime, Duration, Local};
use indexmap::IndexMap;
use once_cell::sync::Lazy;
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

// Cache directories.
//
// Python: `CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"`, i.e.
// `<repo>/cache`. `BASE_DIR` already resolves to the repo root (with a trailing
// slash). The `.mkdir(parents=True, exist_ok=True)` import-time side effect is
// reproduced inside the `SEARCH_CACHE_DIR` / `CONTENT_CACHE_DIR` initializers.
pub static CACHE_DIR: Lazy<PathBuf> = Lazy::new(|| Path::new(BASE_DIR.as_str()).join("cache"));
pub static SEARCH_CACHE_DIR: Lazy<PathBuf> = Lazy::new(|| {
    let p = CACHE_DIR.join("search");
    let _ = std::fs::create_dir_all(&p); // mkdir(parents=True, exist_ok=True)
    p
});
pub static CONTENT_CACHE_DIR: Lazy<PathBuf> = Lazy::new(|| {
    let p = CACHE_DIR.join("content");
    let _ = std::fs::create_dir_all(&p);
    p
});
pub const CACHE_MAX_ENTRIES: usize = 1000;

// Track cache size for LRU eviction.
//
// Python module-level mutable dicts `Dict[str, datetime]`. `IndexMap` preserves
// insertion order so the stable `sorted(...)` tie-breaking below matches Python.
pub static SEARCH_CACHE_INDEX: Lazy<Mutex<IndexMap<String, DateTime<Local>>>> =
    Lazy::new(|| Mutex::new(IndexMap::new()));
pub static CONTENT_CACHE_INDEX: Lazy<Mutex<IndexMap<String, DateTime<Local>>>> =
    Lazy::new(|| Mutex::new(IndexMap::new()));

// Cache metrics (shared across modules) — `{"hits", "misses", "evictions"}`.
pub static CACHE_METRICS: Lazy<Mutex<HashMap<&'static str, i64>>> = Lazy::new(|| {
    let mut m = HashMap::new();
    m.insert("hits", 0);
    m.insert("misses", 0);
    m.insert("evictions", 0);
    Mutex::new(m)
});

/// Add `n` to a `cache_metrics` counter (the `cache_metrics["k"] += n` idiom).
pub fn bump_metric(key: &'static str, n: i64) {
    let mut m = CACHE_METRICS.lock().unwrap();
    *m.entry(key).or_insert(0) += n;
}

/// Generate a unique cache key using SHA-256 hash.
pub fn generate_cache_key(data: &str) -> String {
    // hashlib.sha256(data.encode("utf-8")).hexdigest()
    let mut hasher = Sha256::new();
    hasher.update(data.as_bytes());
    hex::encode(hasher.finalize())
}

/// `f.name.split(".")[0]` — the filename component up to the first dot.
fn stem_before_dot(name: &str) -> String {
    match name.find('.') {
        Some(i) => name[..i].to_string(),
        None => name.to_string(),
    }
}

/// Remove expired cache entries and enforce LRU policy.
pub fn cleanup_cache(
    cache_dir: &Path,
    cache_index: &mut IndexMap<String, DateTime<Local>>,
    max_age: Duration,
) {
    // current_time = datetime.now()
    let current_time = Local::now();
    // files_in_dir = {f.name.split(".")[0]: f for f in cache_dir.glob("*.cache")}
    let mut files_in_dir: IndexMap<String, PathBuf> = IndexMap::new();
    if let Ok(entries) = std::fs::read_dir(cache_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) == Some("cache") {
                if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
                    files_in_dir.insert(stem_before_dot(name), path.clone());
                }
            }
        }
    }

    let mut to_remove: Vec<String> = Vec::new();
    // for key, timestamp in list(cache_index.items()):
    for (key, timestamp) in cache_index.iter() {
        // if current_time - timestamp > max_age or key not in files_in_dir:
        if (current_time - *timestamp) > max_age || !files_in_dir.contains_key(key) {
            to_remove.push(key.clone());
            // if key in files_in_dir: files_in_dir[key].unlink(missing_ok=True)
            if let Some(path) = files_in_dir.get(key) {
                let _ = std::fs::remove_file(path);
            }
        }
    }

    for key in &to_remove {
        // cache_index.pop(key, None)
        cache_index.shift_remove(key);
        // cache_metrics["evictions"] += 1
        bump_metric("evictions", 1);
    }

    if cache_index.len() > CACHE_MAX_ENTRIES {
        // sorted_items = sorted(cache_index.items(), key=lambda x: x[1])
        // Python's sort is stable; IndexMap preserves insertion order, so a
        // stable sort by timestamp reproduces the tie-breaking exactly.
        let mut sorted_items: Vec<(String, DateTime<Local>)> =
            cache_index.iter().map(|(k, v)| (k.clone(), *v)).collect();
        sorted_items.sort_by_key(|item| item.1);
        let excess_count = cache_index.len() - CACHE_MAX_ENTRIES;
        for (key, _) in sorted_items.iter().take(excess_count) {
            // cache_index.pop(key, None)
            cache_index.shift_remove(key);
            // cache_file = cache_dir / f"{key}.cache"; cache_file.unlink(missing_ok=True)
            let cache_file = cache_dir.join(format!("{key}.cache"));
            let _ = std::fs::remove_file(&cache_file);
            // cache_metrics["evictions"] += 1
            bump_metric("evictions", 1);
        }
    }
}
