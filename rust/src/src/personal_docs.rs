// src/personal_docs.rs  <- src/personal_docs.py
//! Personal-document indexing and keyword/vector retrieval.
//!
//! Faithful port of `src/personal_docs.py`.
//!
//! ## Port classification
//!
//! * PURE slices (`PersonalDocsConfig`, `read_text_file`, `split_chunks`,
//!   `tokenize`, `load_personal_index`, `retrieve_personal_keyword`,
//!   `retrieve_personal`, `PersonalDocsManager` directory bookkeeping) — PORT_NOW.
//! * `extract_pdf_text` — PORT_PARTIAL via the `pdf-extract` crate (the BSD
//!   `pypdf` analogue). Returns `""` on any failure, exactly like the Python
//!   (`ImportError` / generic `Exception` -> `""`).
//! * The `rag_manager`-backed paths (`add_directory` indexing, `remove_directory`
//!   targeted delete, `index_all_directories`, vector-first `retrieve`) degrade to
//!   the keyword path when no RAG manager is injected — faithful `Option`
//!   semantics matching Python's `if rag_manager:` / `if self.rag_manager:`
//!   guards. `remove_directory` calls `RagManager::remove_directory` (targeted
//!   delete of just this directory's chunks), NOT `rebuild_index` — the #1660 fix
//!   that avoids wiping the shared collection.
//!
//! ### Feature gate — `web`
//!
//! The whole module is `web`-gated because `extract_pdf_text` needs the
//! `pdf-extract` crate (a `web` dep) and `PersonalDocsManager` carries the RAG
//! manager (the vector RAG cohort is `web`). The genuinely pure free functions
//! (`read_text_file`, `split_chunks`, `tokenize`, `load_personal_index`,
//! `retrieve_personal_keyword`) are nonetheless written so they only use `std` +
//! `walkdir` + `regex`, so they could be lifted to the default build later
//! without change.
//!
//! ### Cross-module decision — the `rag_manager` shape (documented)
//!
//! The contract specifies `rag_manager: Option<Arc<VectorRAG>>`. The Python
//! `rag_manager` is *duck-typed*: `personal_docs` only ever calls
//! `.search(query, k)`, `.index_personal_documents(directory, owner=...)`,
//! `.rebuild_index()`, and `.remove_directory(directory)` on it (and reads the
//! `success` / `indexed_count` keys of the dict results). To keep this module
//! independently compilable and honest
//! about exactly which surface it depends on — and because the concrete
//! `crate::src::rag_vector::VectorRAG` lands in the sibling `rag_vector.rs` /
//! `memory_vector.rs` step — the dependency is modelled as the narrow
//! [`RagManager`] trait below. `VectorRAG` implements `RagManager`, so the
//! `app_initializer` can still inject one shared `Arc<dyn RagManager>` exactly as
//! Python shares one `VectorRAG` instance. `None` reproduces `rag_manager=None`.
//!
//! ### `os.path.abspath` -> `std::path::absolute`
//!
//! Python's `os.path.abspath` normalises against the process CWD without
//! resolving symlinks; `std::path::absolute` is the closest std analogue (it
//! prepends the CWD and lexically normalises without touching the filesystem).
//! `add_directory`'s exclusion prefix-strip and `exclude_file`'s comparison use
//! it the same way the Python does.

use std::collections::HashSet;
use std::path::Path;
use std::sync::Arc;

use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Map, Value};
use walkdir::WalkDir;

use crate::error::PyResult;
use crate::pylog as logger;

// ---------------------------------------------------------------------------
// RAG manager dependency (the duck-typed `rag_manager`)
// ---------------------------------------------------------------------------

/// The narrow surface `personal_docs` uses on its optional RAG manager.
///
/// Python's `rag_manager` is a `VectorRAG` (or `None`) and only three of its
/// methods are touched here. Modelling it as a trait keeps `personal_docs`
/// honest about that surface and lets `rag_vector::VectorRAG` implement it
/// without `personal_docs` taking a hard dependency on a module that lands in a
/// sibling step. Results are `serde_json::Value` dicts to mirror the Python
/// `Dict[str, Any]` returns 1:1 (`result["metadata"]["source"]`,
/// `result["document"]`, `result.get("indexed_count")`, `result.get("success")`).
pub trait RagManager: Send + Sync {
    /// `rag_manager.search(query, k) -> List[Dict[str, Any]]`.
    fn search(&self, query: &str, k: usize) -> PyResult<Vec<Value>>;

    /// `rag_manager.index_personal_documents(directory, owner=owner) -> Dict`.
    fn index_personal_documents(&self, directory: &str, owner: Option<&str>) -> PyResult<Value>;

    /// `rag_manager.rebuild_index() -> bool`.
    fn rebuild_index(&self) -> PyResult<bool>;

    /// `rag_manager.remove_directory(directory) -> Dict`.
    ///
    /// Targeted delete of just this directory's chunks from the shared
    /// collection. `remove_directory` (#1660) calls THIS instead of
    /// `rebuild_index`, which delete+recreated the entire shared collection
    /// (every owner + the base index) and re-indexed only the remaining tracked
    /// dirs — ownerless and never `personal_dir` — a catastrophic wipe. The
    /// concrete `rag_vector::VectorRAG::remove_directory` returns a `Dict`-shaped
    /// `Value`; the result is not inspected here (parity with the Python, which
    /// only swallows exceptions).
    fn remove_directory(&self, directory: &str) -> PyResult<Value>;
}

// ---------------------------------------------------------------------------
// PDF text extraction
// ---------------------------------------------------------------------------

/// `extract_pdf_text(file_path) -> str`.
///
/// Extract text from a PDF. The Python uses `pypdf` (permissive BSD) and returns
/// `""` on `ImportError` or any other exception. The Rust port uses the
/// `pdf-extract` crate (read-only text extraction) and likewise returns `""` on
/// any failure — never a fabricated string.
pub fn extract_pdf_text(file_path: &str) -> String {
    match pdf_extract::extract_text(file_path) {
        Ok(text) => text,
        Err(e) => {
            // Python logs `Failed to extract PDF text from {file_path}: {e}` for
            // the generic-exception branch; the import branch logs a warning.
            // pdf-extract is always present (it is a compiled dep), so only the
            // generic-failure branch is reachable here.
            logger::error(&format!("Failed to extract PDF text from {file_path}: {e}"));
            String::new()
        }
    }
}

/// `markitdown_runtime.MARKITDOWN_EXTS` — formats routed through markitdown.
///
/// PDFs stay on `pdf-extract` (the `pypdf` analogue); plain text/code/json/md
/// stay on the cheaper built-in text path. These are the Office/EPUB formats the
/// Python routes through the optional `markitdown` dependency. The upstream
/// `src/markitdown_runtime.py` has no Rust port yet, so the constant is inlined
/// here (the only consumer in this module) to keep the dispatch faithful without
/// a cross-module dependency the convergence pass would otherwise have to wire.
const MARKITDOWN_EXTS: &[&str] = &[".docx", ".pptx", ".xlsx", ".xls", ".epub"];

/// `extract_office_text(file_path) -> str`.
///
/// Extract text from an Office/EPUB doc via the optional markitdown dependency.
/// Returns `""` when markitdown is missing or extraction fails, mirroring
/// `extract_pdf_text` — the indexer then simply skips the file's content.
///
/// The Python delegates to `markitdown_runtime.convert_to_markdown`, which is an
/// **optional** dependency: when `markitdown` is not installed it logs a warning
/// and returns `None`, and `extract_office_text` coerces that to `""`. The Rust
/// port has no markitdown runtime yet (a separate catch-up step), so this
/// reproduces exactly the "markitdown unavailable" branch — log a warning and
/// return `""` — the same graceful degradation the Python guarantees. When a
/// Rust markitdown equivalent lands, this body delegates to it.
pub fn extract_office_text(file_path: &str) -> String {
    // markitdown is unavailable in the Rust build: mirror the
    // `convert_to_markdown` import-failure branch (warn + return None -> "").
    logger::warning(&format!(
        "markitdown not installed; cannot extract {file_path}"
    ));
    String::new()
}

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

/// `config.STOP_WORDS` — the post-init stop-word set.
///
/// Built from the triple-quoted block split on whitespace (`str.split()`), so
/// the leading/trailing/embedded indentation whitespace is discarded and each
/// bare word becomes a set member.
static STOP_WORDS: Lazy<HashSet<&'static str>> = Lazy::new(|| {
    "the a an is are was were be been being to of in for on at by with from \
     and or if then else when while as it this that those these i you he she \
     we they my your our their me him her us them"
        .split_whitespace()
        .collect()
});

/// `config.DEFAULT_EXTENSIONS = (".txt", ".md", ".json", ".pdf", ".docx",
/// ".pptx", ".xlsx", ".xls", ".epub")`.
const DEFAULT_EXTENSIONS: &[&str] = &[
    ".txt", ".md", ".json", ".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".epub",
];

/// `config.CHUNK_SIZE = 1000`.
const CHUNK_SIZE: usize = 1000;
/// `config.CHUNK_OVERLAP = 200`.
const CHUNK_OVERLAP: usize = 200;

// ---------------------------------------------------------------------------
// Pure free functions
// ---------------------------------------------------------------------------

/// `read_text_file(path) -> str`.
///
/// Read a text file with error handling. Python opens with
/// `encoding="utf-8", errors="ignore"` and returns `""` on any exception. The
/// Rust analogue reads the bytes and lossily decodes as UTF-8 (the
/// `errors="ignore"`-style fallback for invalid bytes), returning `""` on any IO
/// failure.
pub fn read_text_file(path: &str) -> String {
    match std::fs::read(path) {
        Ok(bytes) => {
            // errors="ignore": drop undecodable bytes. `from_utf8_lossy` inserts
            // U+FFFD; to match Python's *drop* semantics we strip those that came
            // from invalid sequences. In practice the corpus is UTF-8 text, so we
            // use the lossy decode (honest, documented) — any divergence is
            // confined to genuinely corrupt bytes.
            String::from_utf8_lossy(&bytes).into_owned()
        }
        Err(_) => String::new(),
    }
}

/// `split_chunks(text, size=1000, overlap=200) -> List[str]`.
///
/// Split text into overlapping chunks. CHAR-based (Python slices the `str` by
/// character index); we operate on a `Vec<char>` so multi-byte characters are
/// counted like Python's `len(str)`.
pub fn split_chunks(text: &str, size: usize, overlap: usize) -> Vec<String> {
    // text = text.strip()
    let text = text.trim();
    // if not text: return []
    if text.is_empty() {
        return Vec::new();
    }
    let chars: Vec<char> = text.chars().collect();
    let n = chars.len();
    let mut chunks: Vec<String> = Vec::new();
    let mut i = 0usize;
    while i < n {
        // j = min(i + size, n)
        let j = std::cmp::min(i + size, n);
        // chunks.append(text[i:j])
        chunks.push(chars[i..j].iter().collect());
        // if j >= n: break
        // Reached the end. Without this, the next start (j - overlap) is still
        // > i, so the loop appended one extra chunk duplicating the last
        // `overlap` chars of the text.
        if j >= n {
            break;
        }
        // i = j - overlap if j - overlap > i else j
        // Python integer subtraction can go negative; `j - overlap > i` is then
        // false, so the `else j` branch is taken. Use signed arithmetic to
        // reproduce that comparison faithfully.
        let next = j as isize - overlap as isize;
        i = if next > i as isize { next as usize } else { j };
    }
    chunks
}

/// `split_chunks` with the config defaults (`size=1000, overlap=200`).
pub fn split_chunks_default(text: &str) -> Vec<String> {
    split_chunks(text, CHUNK_SIZE, CHUNK_OVERLAP)
}

/// `tokenize(s) -> Set[str]`.
///
/// `re.findall(r"[A-Za-z0-9_\-]+", (s or "").lower())`, then drop stop words and
/// single-character tokens. Returns a set (deduped).
pub fn tokenize(s: &str) -> HashSet<String> {
    static TOKEN_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[A-Za-z0-9_\-]+").unwrap());
    let lowered = s.to_lowercase();
    let mut out: HashSet<String> = HashSet::new();
    for m in TOKEN_RE.find_iter(&lowered) {
        let t = m.as_str();
        // if t not in config.STOP_WORDS and len(t) > 1
        if !STOP_WORDS.contains(t) && t.chars().count() > 1 {
            out.insert(t.to_string());
        }
    }
    out
}

/// One indexed file: `{"name", "path", "size", "chunks"}` plus the manager-added
/// `"source_dir"` / overwritten `"name"`. Mirrors the Python `Dict[str, Any]`;
/// kept as a struct for clarity, with the dynamic extras (`source_dir`) tracked
/// explicitly.
#[derive(Debug, Clone)]
pub struct IndexedFile {
    /// `f["name"]` — the display name (relpath, possibly directory-prefixed).
    pub name: String,
    /// `f["path"]` — the absolute/real path on disk.
    pub path: String,
    /// `f["size"]` — `os.path.getsize`.
    pub size: u64,
    /// `f["chunks"]` — the overlapping text chunks.
    pub chunks: Vec<String>,
    /// `f["source_dir"]` — set by `PersonalDocsManager.refresh_index` (`None`
    /// until then, like the bare dict that lacks the key).
    pub source_dir: Option<String>,
}

/// `load_personal_index(personal_dir, extensions=(".txt", ".md", ".json"))`.
///
/// Walk `personal_dir`, indexing files whose name ends in one of `extensions`.
/// Within each directory the names are visited in sorted order (Python
/// `for name in sorted(names)`), and `display` is the path relative to
/// `personal_dir`.
pub fn load_personal_index(personal_dir: &str, extensions: &[&str]) -> Vec<IndexedFile> {
    let mut files: Vec<IndexedFile> = Vec::new();

    // os.walk yields (root, dirs, names) top-down. We reproduce the
    // per-directory `sorted(names)` ordering: WalkDir with sorted entries groups
    // files under their parent, and within a directory we sort the file names.
    // Collect by directory to apply `sorted(names)` exactly.
    let mut by_dir: Vec<(std::path::PathBuf, Vec<std::fs::DirEntry>)> = Vec::new();

    // Use WalkDir to enumerate directories top-down (os.walk default), then read
    // each directory's own entries so we can `sorted(names)` like the Python.
    for dir_entry in WalkDir::new(personal_dir)
        .sort_by_file_name()
        .into_iter()
        .filter_map(|e| e.ok())
    {
        if dir_entry.file_type().is_dir() {
            let root = dir_entry.path().to_path_buf();
            let mut names: Vec<std::fs::DirEntry> = match std::fs::read_dir(&root) {
                Ok(rd) => rd.filter_map(|e| e.ok()).collect(),
                Err(_) => Vec::new(),
            };
            // sorted(names) — by file name.
            names.sort_by_key(|e| e.file_name());
            by_dir.push((root, names));
        }
    }

    for (root, names) in by_dir {
        for entry in names {
            let p = entry.path();
            // if not os.path.isfile(p): continue
            if !p.is_file() {
                continue;
            }
            let name = entry.file_name().to_string_lossy().into_owned();
            let name_lower = name.to_lowercase();
            // if not any(name.lower().endswith(ext) for ext in extensions): continue
            if !extensions.iter().any(|ext| name_lower.ends_with(ext)) {
                continue;
            }
            let p_str = p.to_string_lossy().into_owned();
            // size = os.path.getsize(p)
            let size = std::fs::metadata(&p).map(|m| m.len()).unwrap_or(0);
            // ext = os.path.splitext(name)[1].lower()
            let ext = splitext_suffix(&name).to_lowercase();
            // if ext == ".pdf": extract_pdf_text
            // elif ext in MARKITDOWN_EXTS: extract_office_text
            // else: read_text_file
            let text = if ext == ".pdf" {
                extract_pdf_text(&p_str)
            } else if MARKITDOWN_EXTS.contains(&ext.as_str()) {
                extract_office_text(&p_str)
            } else {
                read_text_file(&p_str)
            };
            // chunks = split_chunks(text)
            let chunks = split_chunks_default(&text);
            // display = os.path.relpath(p, personal_dir)
            let display = relpath(&p, personal_dir);
            files.push(IndexedFile {
                name: display,
                path: p_str,
                size,
                chunks,
                source_dir: None,
            });
        }
        let _ = &root; // root used above for read_dir; silence unused on some paths
    }

    files
}

/// `load_personal_index` with the default extensions.
pub fn load_personal_index_default(personal_dir: &str) -> Vec<IndexedFile> {
    load_personal_index(personal_dir, DEFAULT_EXTENSIONS)
}

/// `retrieve_personal_keyword(personal_index, query, k=5) -> List[str]`.
///
/// Keyword search: score each chunk by `len(query_tokens & chunk_tokens)`,
/// keep positives, sort by score descending (stable), take `k`, and format each
/// as `"[{name} :: chunk {idx+1}]\n{chunk}"`.
pub fn retrieve_personal_keyword(
    personal_index: &[IndexedFile],
    query: &str,
    k: usize,
) -> Vec<String> {
    // q = tokenize(query); if not q: return []
    let q = tokenize(query);
    if q.is_empty() {
        return Vec::new();
    }

    // scored: (score, name, idx, chunk)
    let mut scored: Vec<(usize, String, usize, String)> = Vec::new();
    for f in personal_index {
        for (idx, ch) in f.chunks.iter().enumerate() {
            // score = len(q & tokenize(ch))
            let ch_tokens = tokenize(ch);
            let score = q.intersection(&ch_tokens).count();
            if score > 0 {
                scored.push((score, f.name.clone(), idx, ch.clone()));
            }
        }
    }
    // scored.sort(key=lambda x: x[0], reverse=True) — Python's sort is stable.
    // sort_by_key with Reverse(score) keeps stability for equal keys
    // (preserving the original push order, like Python's stable reverse).
    scored.sort_by_key(|x| std::cmp::Reverse(x.0));

    // out for the top-k
    scored
        .into_iter()
        .take(k)
        .map(|(_s, fname, idx, ch)| format!("[{fname} :: chunk {}]\n{ch}", idx + 1))
        .collect()
}

/// `retrieve_personal(personal_index, query, k=5, rag_manager=None)`.
///
/// Vector search first (when a RAG manager is present), falling back to keyword
/// search on empty results or any error.
pub fn retrieve_personal(
    personal_index: &[IndexedFile],
    query: &str,
    k: usize,
    rag_manager: Option<&Arc<dyn RagManager>>,
) -> Vec<String> {
    // if not query: return []
    if query.is_empty() {
        return Vec::new();
    }

    // First try vector search if RAGManager is available.
    if let Some(rag) = rag_manager {
        match rag.search(query, k) {
            Ok(vector_results) if !vector_results.is_empty() => {
                let mut out: Vec<String> = Vec::new();
                for result in &vector_results {
                    // source = result["metadata"].get("source", "")
                    let source = result
                        .get("metadata")
                        .and_then(|m| m.get("source"))
                        .and_then(|s| s.as_str())
                        .unwrap_or("");
                    // filename = os.path.basename(source)
                    let filename = basename(source);
                    // result['document']
                    let document = result.get("document").and_then(|d| d.as_str()).unwrap_or("");
                    // f"[{filename} :: vector search]\n{document}"
                    out.push(format!("[{filename} :: vector search]\n{document}"));
                }
                return out;
            }
            Ok(_) => {
                // Empty vector results -> fall through to keyword search (Python:
                // the `if vector_results:` guard simply does not early-return).
            }
            Err(e) => {
                logger::warning(&format!(
                    "Vector search failed, falling back to keyword search: {e}"
                ));
            }
        }
    }

    // Fall back to keyword search.
    retrieve_personal_keyword(personal_index, query, k)
}

// ---------------------------------------------------------------------------
// PersonalDocsManager
// ---------------------------------------------------------------------------

/// The mutable bookkeeping a `PersonalDocsManager` carries (`self.index`,
/// `self.indexed_directories`, `self.excluded_files`).
///
/// In Python the manager is a single shared instance whose attributes the route
/// handlers mutate in place. The Rust app holds it as one `Arc<PersonalDocsManager>`
/// shared across every handler (no `Mutex` field in `AppState`), so the manager
/// owns its own interior mutability: this state lives behind a `std::sync::Mutex`,
/// and every method takes `&self` (the live Python shape — `refresh_index`,
/// `add_directory`, `remove_directory`, `exclude_file` all mutate the one shared
/// instance). The immutable config (`personal_dir`, `rag_manager`, the two JSON
/// paths) stays in plain fields.
#[derive(Default)]
struct ManagerState {
    /// `self.index`.
    index: Vec<IndexedFile>,
    /// `self.indexed_directories`.
    indexed_directories: Vec<String>,
    /// `self.excluded_files` — set of absolute paths excluded from listing.
    excluded_files: HashSet<String>,
}

/// `PersonalDocsManager` — personal-document indexing and retrieval.
pub struct PersonalDocsManager {
    /// `self.personal_dir`.
    personal_dir: String,
    /// `self.rag_manager` — `None` reproduces the Python `rag_manager=None`.
    rag_manager: Option<Arc<dyn RagManager>>,
    /// `self.directories_file = <personal_dir>/indexed_directories.json`.
    directories_file: String,
    /// `self._excluded_file = <personal_dir>/excluded_files.json`.
    excluded_file: String,
    /// The mutable bookkeeping (`index` / `indexed_directories` / `excluded_files`),
    /// behind a `Mutex` so the shared `Arc<PersonalDocsManager>` can mutate it.
    state: std::sync::Mutex<ManagerState>,
}

impl PersonalDocsManager {
    /// `__init__(self, personal_dir, rag_manager=None)`.
    pub fn new(personal_dir: &str, rag_manager: Option<Arc<dyn RagManager>>) -> Self {
        let directories_file = join(personal_dir, "indexed_directories.json");
        let excluded_file = join(personal_dir, "excluded_files.json");
        let mgr = PersonalDocsManager {
            personal_dir: personal_dir.to_string(),
            rag_manager,
            directories_file,
            excluded_file,
            state: std::sync::Mutex::new(ManagerState::default()),
        };
        mgr.load_directories();
        mgr.load_excluded();
        mgr.refresh_index();
        mgr
    }

    /// Lock the interior state, recovering from a poisoned mutex (a panic in a
    /// prior holder must not wedge the whole manager — the data is plain JSON
    /// bookkeeping, safe to keep using).
    fn lock(&self) -> std::sync::MutexGuard<'_, ManagerState> {
        self.state.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// `load_directories(self)`.
    pub fn load_directories(&self) {
        // try: ... except Exception: self.indexed_directories = []
        let loaded = (|| -> Option<Vec<String>> {
            if !Path::new(&self.directories_file).exists() {
                return Some(Vec::new());
            }
            let text = std::fs::read_to_string(&self.directories_file).ok()?;
            let v: Value = serde_json::from_str(&text).ok()?;
            let arr = v.as_array()?;
            Some(
                arr.iter()
                    .filter_map(|x| x.as_str().map(|s| s.to_string()))
                    .collect(),
            )
        })();
        match loaded {
            Some(dirs) => {
                let was_present = Path::new(&self.directories_file).exists();
                let len = dirs.len();
                self.lock().indexed_directories = dirs;
                if was_present {
                    logger::info(&format!("Loaded {len} indexed directories"));
                }
            }
            None => {
                logger::error("Error loading directories");
                self.lock().indexed_directories = Vec::new();
            }
        }
    }

    /// `save_directories(self)`.
    pub fn save_directories(&self) {
        let arr: Vec<Value> = self
            .lock()
            .indexed_directories
            .iter()
            .map(|d| Value::String(d.clone()))
            .collect();
        let count = arr.len();
        // json.dump(..., indent=2)
        match serde_json::to_string_pretty(&Value::Array(arr)) {
            Ok(text) => match std::fs::write(&self.directories_file, text) {
                Ok(()) => logger::info(&format!("Saved {count} indexed directories")),
                Err(e) => logger::error(&format!("Error saving directories: {e}")),
            },
            Err(e) => logger::error(&format!("Error saving directories: {e}")),
        }
    }

    /// `_load_excluded(self)`.
    fn load_excluded(&self) {
        let loaded = (|| -> Option<HashSet<String>> {
            if !Path::new(&self.excluded_file).exists() {
                return Some(HashSet::new());
            }
            let text = std::fs::read_to_string(&self.excluded_file).ok()?;
            let v: Value = serde_json::from_str(&text).ok()?;
            let arr = v.as_array()?;
            Some(
                arr.iter()
                    .filter_map(|x| x.as_str().map(|s| s.to_string()))
                    .collect(),
            )
        })();
        match loaded {
            Some(set) => self.lock().excluded_files = set,
            None => {
                logger::error("Error loading excluded files");
                self.lock().excluded_files = HashSet::new();
            }
        }
    }

    /// `_save_excluded(self)`.
    fn save_excluded(&self) {
        let arr: Vec<Value> = self
            .lock()
            .excluded_files
            .iter()
            .map(|p| Value::String(p.clone()))
            .collect();
        // json.dump(list(self.excluded_files), f) — no indent.
        match serde_json::to_string(&Value::Array(arr)) {
            Ok(text) => {
                if let Err(e) = std::fs::write(&self.excluded_file, text) {
                    logger::error(&format!("Error saving excluded files: {e}"));
                }
            }
            Err(e) => logger::error(&format!("Error saving excluded files: {e}")),
        }
    }

    /// `exclude_file(self, filepath)`.
    pub fn exclude_file(&self, filepath: &str) {
        let abs = abspath(filepath);
        {
            let mut st = self.lock();
            st.excluded_files.insert(abs.clone());
        }
        self.save_excluded();
        // self.index = [f for f in self.index if abspath(f["path"]) != abspath(filepath)]
        self.lock().index.retain(|f| abspath(&f.path) != abs);
    }

    /// `add_directory(self, directory, *, index=True, owner=None)`.
    pub fn add_directory(&self, directory: &str, index: bool, owner: Option<&str>) {
        // directory = os.path.abspath(directory)
        let directory = abspath(directory);

        // Clear any exclusions for files in this directory. Match on a path
        // boundary (the directory itself or paths under it) rather than a raw
        // string prefix: a bare `starts_with(directory)` also matches sibling
        // directories that merely share a name prefix (e.g. adding `/docs` would
        // wrongly un-exclude files under `/docs2`).
        // self.excluded_files = {
        //     p for p in self.excluded_files
        //     if not (p == directory or p.startswith(directory + os.sep))
        // }
        {
            // `directory + os.sep` — the directory with a trailing path separator.
            let dir_with_sep = format!("{directory}{}", std::path::MAIN_SEPARATOR);
            let mut st = self.lock();
            st.excluded_files
                .retain(|p| !(p == &directory || p.starts_with(&dir_with_sep)));
        }
        self.save_excluded();

        let already = self.lock().indexed_directories.contains(&directory);
        if !already {
            self.lock().indexed_directories.push(directory.clone());
            self.save_directories();
            logger::info(&format!("Added directory to tracking: {directory}"));

            // If RAG manager is available, index the directory immediately.
            if index {
                if let Some(rag) = &self.rag_manager {
                    match rag.index_personal_documents(&directory, owner) {
                        Ok(result) => {
                            let indexed_count = result
                                .get("indexed_count")
                                .and_then(|v| v.as_i64())
                                .unwrap_or(0);
                            logger::info(&format!(
                                "Indexed {indexed_count} chunks from {directory}"
                            ));
                        }
                        Err(e) => {
                            logger::error(&format!("Failed to index directory {directory}: {e}"));
                        }
                    }
                }
            }

            // Refresh the local index to include the new directory.
            self.refresh_index();
        } else {
            logger::info(&format!("Directory already indexed: {directory}"));
        }
    }

    /// `remove_directory(self, directory)`.
    pub fn remove_directory(&self, directory: &str) {
        // directory = os.path.abspath(directory)
        let directory = abspath(directory);

        let pos = self
            .lock()
            .indexed_directories
            .iter()
            .position(|d| d == &directory);
        if let Some(pos) = pos {
            self.lock().indexed_directories.remove(pos);
            self.save_directories();
            logger::info(&format!("Removed directory from tracking: {directory}"));

            // Refresh the index to exclude the removed directory.
            self.refresh_index();

            // Targeted delete of just this directory's chunks. This previously
            // called rag_manager.rebuild_index(), which delete+recreates the
            // entire shared collection (every owner + the base index) and then
            // re-indexed only the remaining tracked dirs — ownerless and never
            // personal_dir — a catastrophic wipe (#1660). remove_directory now
            // removes exactly this directory's chunks and leaves the rest intact.
            if let Some(rag) = &self.rag_manager {
                if let Err(e) = rag.remove_directory(&directory) {
                    logger::error(&format!("Failed to remove directory from RAG index: {e}"));
                }
            }
        } else {
            logger::info(&format!("Directory not in index: {directory}"));
        }
    }

    /// `get_indexed_directories(self)`.
    pub fn get_indexed_directories(&self) -> Vec<String> {
        self.lock().indexed_directories.clone()
    }

    /// `refresh_index(self)`.
    pub fn refresh_index(&self) {
        // Build the fresh index outside the lock (the directory walk is the slow
        // part), then swap it in under one short critical section.
        let excluded: HashSet<String> = self.lock().excluded_files.clone();
        let dirs: Vec<String> = self.lock().indexed_directories.clone();

        let mut new_index: Vec<IndexedFile> = Vec::new();

        // Index the base personal directory.
        let base_files = load_personal_index_default(&self.personal_dir);
        for mut f in base_files {
            // if abspath(f["path"]) in self.excluded_files: continue
            if excluded.contains(&abspath(&f.path)) {
                continue;
            }
            f.source_dir = Some(self.personal_dir.clone());
            new_index.push(f);
        }

        // Index additional directories.
        for directory in &dirs {
            if !Path::new(directory).exists() {
                logger::warning(&format!("Directory no longer exists: {directory}"));
                continue;
            }
            if !Path::new(directory).is_dir() {
                logger::warning(&format!("Path is not a directory: {directory}"));
                continue;
            }

            let dir_files = load_personal_index_default(directory);
            // f['name'] = f"{os.path.basename(directory)}/{f['name']}"
            let dir_base = basename(directory);
            for mut f in dir_files {
                if excluded.contains(&abspath(&f.path)) {
                    continue;
                }
                f.source_dir = Some(directory.clone());
                f.name = format!("{dir_base}/{}", f.name);
                new_index.push(f);
            }
        }

        let doc_count = new_index.len();
        self.lock().index = new_index;

        logger::info(&format!(
            "Refreshed index: {} documents from {} directories",
            doc_count,
            dirs.len() + 1
        ));
    }

    /// `retrieve(self, query, k=5) -> List[str]`.
    pub fn retrieve(&self, query: &str, k: usize) -> Vec<String> {
        let index = self.lock().index.clone();
        retrieve_personal(&index, query, k, self.rag_manager.as_ref())
    }

    /// `get_file_list(self) -> List[Dict[str, Any]]`.
    ///
    /// `[{"name": f["name"], "size": f["size"]} for f in self.index]`.
    pub fn get_file_list(&self) -> Vec<Value> {
        self.lock()
            .index
            .iter()
            .map(|f| json!({ "name": f.name, "size": f.size }))
            .collect()
    }

    /// `[{"name": f["name"], "size": f["size"], "path": f.get("path", "")} for f
    /// in self.index]` — the listing shape `routes/personal_routes.py`'s
    /// `api_personal_list` builds directly off `personal_docs_manager.index`.
    ///
    /// Distinct from [`get_file_list`] (name + size only): this also carries the
    /// `path`, which the route's enhanced listing includes.
    pub fn list_index_entries(&self) -> Vec<Value> {
        self.lock()
            .index
            .iter()
            .map(|f| json!({ "name": f.name, "size": f.size, "path": f.path }))
            .collect()
    }

    /// `len(personal_docs_manager.index)` — the count the `/api/personal/reload`
    /// route returns after `refresh_index`.
    pub fn index_len(&self) -> usize {
        self.lock().index.len()
    }

    /// The display `name` of every indexed file, in `self.index` order — the
    /// `[f["name"] for f in self.index]` the built-in RAG MCP server
    /// (`crate::mcp_servers::rag_server`) iterates to format its `list` action's
    /// `**Indexed files (N):**` block.
    ///
    /// The Python `manage_rag` `list` handler reads `files =
    /// getattr(_personal_docs_manager, 'index', None) or []` and then `for f in
    /// files[:50]: fname = f.get("name", str(f))`. `index_len()` only exposes the
    /// count; this exposes the names. The full list is returned (no `[:50]` cap) so
    /// the consumer can both slice the first 50 AND report `len(files) - 50` more,
    /// exactly as Python does. Pure read of the locked state; no behavior change.
    pub fn index_names(&self) -> Vec<String> {
        self.lock().index.iter().map(|f| f.name.clone()).collect()
    }

    /// `get_stats(self) -> Dict[str, Any]`.
    pub fn get_stats(&self) -> Value {
        let st = self.lock();
        let total_docs = st.index.len();
        // sum(len(doc.get('chunks', [])) for doc in self.index)
        let total_chunks: usize = st.index.iter().map(|f| f.chunks.len()).sum();
        // sum(doc.get('size', 0) for doc in self.index)
        let total_size: u64 = st.index.iter().map(|f| f.size).sum();

        // file types: count by os.path.splitext(doc['path'])[1]
        // serde_json preserve_order keeps first-seen extension order, like the
        // Python dict insertion order.
        let mut extensions: Map<String, Value> = Map::new();
        for doc in &st.index {
            let ext = splitext_suffix(&doc.path);
            let entry = extensions.entry(ext).or_insert(Value::from(0i64));
            let cur = entry.as_i64().unwrap_or(0);
            *entry = Value::from(cur + 1);
        }

        // round(total_size / (1024 * 1024), 2)
        let total_size_mb = round2(total_size as f64 / (1024.0 * 1024.0));

        let additional: Vec<Value> = st
            .indexed_directories
            .iter()
            .map(|d| Value::String(d.clone()))
            .collect();

        json!({
            "total_documents": total_docs,
            "total_chunks": total_chunks,
            "total_size_bytes": total_size,
            "total_size_mb": total_size_mb,
            "file_types": Value::Object(extensions),
            "directories_count": st.indexed_directories.len() + 1,
            "base_directory": self.personal_dir,
            "additional_directories": Value::Array(additional),
        })
    }

    /// `index_all_directories(self)`.
    ///
    /// Returns `None` (the Python `return` with no value) when no RAG manager is
    /// available; otherwise `Some({"success", "failed"})`.
    pub fn index_all_directories(&self) -> Option<Value> {
        let rag = match &self.rag_manager {
            Some(r) => r,
            None => {
                logger::warning("No RAG manager available for indexing");
                return None;
            }
        };

        let mut success_count = 0i64;
        let mut failure_count = 0i64;

        // Index the base personal directory.
        match rag.index_personal_documents(&self.personal_dir, None) {
            Ok(result) => {
                if result.get("success").and_then(|v| v.as_bool()).unwrap_or(false) {
                    success_count += 1;
                    logger::info(&format!("Indexed base directory: {}", self.personal_dir));
                }
            }
            Err(e) => {
                failure_count += 1;
                logger::error(&format!(
                    "Failed to index base directory {}: {e}",
                    self.personal_dir
                ));
            }
        }

        // Index additional directories.
        let dirs = self.lock().indexed_directories.clone();
        for directory in &dirs {
            if !Path::new(directory).exists() {
                logger::warning(&format!("Skipping non-existent directory: {directory}"));
                failure_count += 1;
                continue;
            }
            match rag.index_personal_documents(directory, None) {
                Ok(result) => {
                    if result.get("success").and_then(|v| v.as_bool()).unwrap_or(false) {
                        success_count += 1;
                        logger::info(&format!("Indexed directory: {directory}"));
                    } else {
                        failure_count += 1;
                        let message = result
                            .get("message")
                            .and_then(|m| m.as_str())
                            .unwrap_or("None");
                        logger::error(&format!(
                            "Failed to index directory {directory}: {message}"
                        ));
                    }
                }
                Err(e) => {
                    failure_count += 1;
                    logger::error(&format!("Failed to index directory {directory}: {e}"));
                }
            }
        }

        logger::info(&format!(
            "Indexing complete: {success_count} succeeded, {failure_count} failed"
        ));
        Some(json!({ "success": success_count, "failed": failure_count }))
    }
}

// ---------------------------------------------------------------------------
// Path helpers (os.path analogues)
// ---------------------------------------------------------------------------

/// `os.path.join(a, b)`.
fn join(a: &str, b: &str) -> String {
    Path::new(a).join(b).to_string_lossy().into_owned()
}

/// `os.path.basename(p)`.
fn basename(p: &str) -> String {
    crate::pyos::path::basename(p)
}

/// `os.path.abspath(p)` — prepend the CWD and lexically normalise without
/// resolving symlinks. `std::path::absolute` is the std analogue.
fn abspath(p: &str) -> String {
    match std::path::absolute(p) {
        Ok(pb) => pb.to_string_lossy().into_owned(),
        // On the rare failure (e.g. empty path with no CWD), fall back to the
        // raw input so the comparison still behaves like a stringly path.
        Err(_) => p.to_string(),
    }
}

/// `os.path.relpath(p, start)` — the path of `p` relative to `start`.
///
/// Python returns a string with a leading `..` walk when `p` is outside `start`.
/// `load_personal_index` always passes a `p` *under* `personal_dir`, so the
/// common case is a simple strip of the `start` prefix; we still use the std
/// `strip_prefix` and fall back to the file name on mismatch.
fn relpath(p: &Path, start: &str) -> String {
    let start_path = Path::new(start);
    match p.strip_prefix(start_path) {
        Ok(rel) => rel.to_string_lossy().into_owned(),
        Err(_) => p
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| p.to_string_lossy().into_owned()),
    }
}

/// `os.path.splitext(path)[1]` — the extension including the leading dot, or
/// `""` when there is none. Matches CPython: a leading-dot file like `.bashrc`
/// has no extension, and only the *last* dot in the basename counts.
fn splitext_suffix(path: &str) -> String {
    let base = basename(path);
    // Leading dots are part of the name, not an extension (CPython behaviour).
    let stripped = base.trim_start_matches('.');
    let lead = base.len() - stripped.len();
    match stripped.rfind('.') {
        Some(idx) => base[lead + idx..].to_string(),
        None => String::new(),
    }
}

/// `round(x, 2)` with Python's banker's rounding (round-half-to-even).
fn round2(x: f64) -> f64 {
    let scaled = x * 100.0;
    let rounded = round_half_even(scaled);
    rounded / 100.0
}

/// Round-half-to-even (Python 3 `round`).
fn round_half_even(x: f64) -> f64 {
    let floor = x.floor();
    let diff = x - floor;
    if diff < 0.5 {
        floor
    } else if diff > 0.5 {
        floor + 1.0
    } else {
        // Exactly .5 -> round to even.
        if (floor as i64) % 2 == 0 {
            floor
        } else {
            floor + 1.0
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tokenize_drops_stopwords_and_singletons() {
        let toks = tokenize("The quick a brown FOX-1 i");
        // "the", "a", "i" are stop words; "x" alone would be len 1 but here
        // tokens are quick, brown, fox-1.
        assert!(toks.contains("quick"));
        assert!(toks.contains("brown"));
        assert!(toks.contains("fox-1"));
        assert!(!toks.contains("the"));
        assert!(!toks.contains("a"));
        assert!(!toks.contains("i"));
    }

    #[test]
    fn tokenize_empty_is_empty() {
        assert!(tokenize("").is_empty());
        assert!(tokenize("   ").is_empty());
    }

    #[test]
    fn split_chunks_empty() {
        assert!(split_chunks("   ", 1000, 200).is_empty());
        assert!(split_chunks("", 1000, 200).is_empty());
    }

    #[test]
    fn split_chunks_no_overlap_when_small() {
        let chunks = split_chunks("hello world", 1000, 200);
        assert_eq!(chunks, vec!["hello world".to_string()]);
    }

    #[test]
    fn split_chunks_overlap_arithmetic() {
        // size=5, overlap=2: text of 11 chars "abcdefghijk"
        // i=0 j=5 -> "abcde"; j<n; next=3>0 -> i=3
        // i=3 j=8 -> "defgh"; j<n; next=6>3 -> i=6
        // i=6 j=11=n -> "ghijk"; j>=n -> break.
        // The `if j >= n { break }` drops what used to be a trailing duplicate
        // "jk" chunk (the last `overlap` chars). NEW correct behavior: no "jk".
        let chunks = split_chunks("abcdefghijk", 5, 2);
        assert_eq!(chunks, vec!["abcde", "defgh", "ghijk"]);
    }

    #[test]
    fn split_chunks_break_drops_duplicate_final_chunk() {
        // Regression for the duplicate-final-chunk bug. size=4, overlap=2,
        // text="abcdef" (n=6):
        //   i=0 j=4 -> "abcd"; j<n; next=2>0 -> i=2
        //   i=2 j=6=n -> "cdef"; j>=n -> break.
        // Without the break the loop would set i = j-overlap = 4, then push
        // "ef" — a duplicate of the last `overlap` chars. With the break the
        // final chunk is dropped.
        let chunks = split_chunks("abcdef", 4, 2);
        assert_eq!(chunks, vec!["abcd", "cdef"]);
        assert!(!chunks.contains(&"ef".to_string()));
    }

    #[test]
    fn split_chunks_overlap_ge_size_falls_to_j() {
        // size=3, overlap=3: next = j-3; j-3>i never holds, so i jumps to j each
        // step (no infinite loop). "abcdef" -> "abc","def".
        let chunks = split_chunks("abcdef", 3, 3);
        assert_eq!(chunks, vec!["abc", "def"]);
    }

    #[test]
    fn retrieve_keyword_formats_and_sorts() {
        let idx = vec![IndexedFile {
            name: "notes.txt".to_string(),
            path: "/tmp/notes.txt".to_string(),
            size: 0,
            chunks: vec![
                "alpha beta gamma".to_string(),
                "alpha delta".to_string(),
            ],
            source_dir: None,
        }];
        let out = retrieve_personal_keyword(&idx, "alpha beta", 5);
        // chunk 0 matches both alpha & beta (score 2), chunk 1 matches alpha (1).
        assert_eq!(out.len(), 2);
        assert!(out[0].starts_with("[notes.txt :: chunk 1]\n"));
        assert!(out[1].starts_with("[notes.txt :: chunk 2]\n"));
    }

    #[test]
    fn retrieve_keyword_empty_query() {
        let idx: Vec<IndexedFile> = Vec::new();
        assert!(retrieve_personal_keyword(&idx, "", 5).is_empty());
    }

    #[test]
    fn retrieve_personal_no_rag_uses_keyword() {
        let idx = vec![IndexedFile {
            name: "a.txt".to_string(),
            path: "/tmp/a.txt".to_string(),
            size: 0,
            chunks: vec!["zebra unicorn".to_string()],
            source_dir: None,
        }];
        let out = retrieve_personal(&idx, "unicorn", 5, None);
        assert_eq!(out.len(), 1);
        assert!(out[0].contains("zebra unicorn"));
    }

    #[test]
    fn splitext_suffix_cases() {
        assert_eq!(splitext_suffix("/x/y/file.txt"), ".txt");
        assert_eq!(splitext_suffix("/x/y/file"), "");
        assert_eq!(splitext_suffix("/x/y/.bashrc"), "");
        assert_eq!(splitext_suffix("/x/y/a.b.c"), ".c");
    }

    #[test]
    fn round2_banker() {
        // 2.5/100 style: ensure half-even at the 2-dp scale.
        assert_eq!(round2(0.125), 0.12); // 12.5 -> even 12
        assert_eq!(round2(0.135), 0.14); // 13.5 -> even 14
    }

    struct FakeRag {
        results: Vec<Value>,
    }
    impl RagManager for FakeRag {
        fn search(&self, _query: &str, _k: usize) -> PyResult<Vec<Value>> {
            Ok(self.results.clone())
        }
        fn index_personal_documents(&self, _d: &str, _o: Option<&str>) -> PyResult<Value> {
            Ok(json!({"success": true, "indexed_count": 0}))
        }
        fn rebuild_index(&self) -> PyResult<bool> {
            Ok(true)
        }
        fn remove_directory(&self, _directory: &str) -> PyResult<Value> {
            Ok(json!({"success": true, "removed_count": 0}))
        }
    }

    #[test]
    fn retrieve_personal_vector_path_formats() {
        let rag: Arc<dyn RagManager> = Arc::new(FakeRag {
            results: vec![json!({
                "document": "the answer",
                "metadata": {"source": "/docs/answers.txt"},
            })],
        });
        let idx: Vec<IndexedFile> = Vec::new();
        let out = retrieve_personal(&idx, "answer", 5, Some(&rag));
        assert_eq!(out.len(), 1);
        assert_eq!(out[0], "[answers.txt :: vector search]\nthe answer");
    }

    #[test]
    fn retrieve_personal_empty_vector_falls_back() {
        let rag: Arc<dyn RagManager> = Arc::new(FakeRag { results: vec![] });
        let idx = vec![IndexedFile {
            name: "kw.txt".to_string(),
            path: "/tmp/kw.txt".to_string(),
            size: 0,
            chunks: vec!["fallback keyword hit".to_string()],
            source_dir: None,
        }];
        let out = retrieve_personal(&idx, "keyword", 5, Some(&rag));
        assert_eq!(out.len(), 1);
        assert!(out[0].contains("fallback keyword hit"));
    }

    #[test]
    fn index_names_returns_display_names_in_index_order() {
        // The built-in RAG MCP server's `list` action reads
        // `_personal_docs_manager.index` and formats `f.get("name")` per file.
        // `index_names` is the accessor that surfaces exactly those display names.
        let dir = tempfile::tempdir().expect("tempdir");
        let base = dir.path().to_str().unwrap();
        // Two indexable files (sorted-name order: alpha.txt before beta.md) plus a
        // non-matching extension that must NOT appear.
        std::fs::write(dir.path().join("alpha.txt"), "alpha contents").unwrap();
        std::fs::write(dir.path().join("beta.md"), "beta contents").unwrap();
        std::fs::write(dir.path().join("ignore.bin"), "skip me").unwrap();

        let mgr = PersonalDocsManager::new(base, None);
        let names = mgr.index_names();
        // load_personal_index visits names in `sorted(names)` order; index_names
        // preserves self.index order. The .bin file is filtered out.
        assert_eq!(names, vec!["alpha.txt".to_string(), "beta.md".to_string()]);
        // Count parity with the existing index_len accessor.
        assert_eq!(names.len(), mgr.index_len());
    }

    // ----- new parity tests -----------------------------------------------

    /// A RAG spy that records which mutation method `remove_directory` invokes.
    /// #1660: the manager must call `remove_directory` (targeted delete), NOT
    /// `rebuild_index` (the catastrophic full-collection wipe).
    struct SpyRag {
        rebuilt: std::sync::atomic::AtomicUsize,
        removed: std::sync::Mutex<Vec<String>>,
    }
    impl SpyRag {
        fn new() -> Self {
            SpyRag {
                rebuilt: std::sync::atomic::AtomicUsize::new(0),
                removed: std::sync::Mutex::new(Vec::new()),
            }
        }
    }
    impl RagManager for SpyRag {
        fn search(&self, _query: &str, _k: usize) -> PyResult<Vec<Value>> {
            Ok(Vec::new())
        }
        fn index_personal_documents(&self, _d: &str, _o: Option<&str>) -> PyResult<Value> {
            Ok(json!({"success": true, "indexed_count": 0}))
        }
        fn rebuild_index(&self) -> PyResult<bool> {
            self.rebuilt
                .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            Ok(true)
        }
        fn remove_directory(&self, directory: &str) -> PyResult<Value> {
            self.removed.lock().unwrap().push(directory.to_string());
            Ok(json!({"success": true, "removed_count": 0}))
        }
    }

    #[test]
    fn remove_directory_calls_targeted_remove_not_rebuild() {
        // #1660 regression: removing a tracked directory must delete only that
        // directory's chunks (rag.remove_directory) and must NOT rebuild the
        // whole shared collection (rag.rebuild_index).
        let base = tempfile::tempdir().expect("base");
        let extra = tempfile::tempdir().expect("extra");
        let base_path = base.path().to_str().unwrap().to_string();
        let extra_path = extra.path().to_str().unwrap().to_string();

        let spy = Arc::new(SpyRag::new());
        let rag: Arc<dyn RagManager> = spy.clone();
        let mgr = PersonalDocsManager::new(&base_path, Some(rag));

        // Track the extra dir, then remove it.
        mgr.add_directory(&extra_path, false, None);
        mgr.remove_directory(&extra_path);

        // rebuild_index must NOT have been called by remove_directory.
        assert_eq!(
            spy.rebuilt.load(std::sync::atomic::Ordering::SeqCst),
            0,
            "remove_directory must not rebuild the whole collection (#1660)"
        );
        // remove_directory must have targeted exactly the removed dir (abspath'd).
        let removed = spy.removed.lock().unwrap().clone();
        assert_eq!(removed, vec![abspath(&extra_path)]);
        // The directory is no longer tracked.
        assert!(!mgr.get_indexed_directories().contains(&abspath(&extra_path)));
    }

    #[test]
    fn remove_directory_untracked_is_noop_for_rag() {
        // Removing a directory that was never tracked must not touch the RAG
        // manager at all (Python: the `if directory in self.indexed_directories`
        // guard short-circuits to the "not in index" log).
        let base = tempfile::tempdir().expect("base");
        let base_path = base.path().to_str().unwrap().to_string();
        let spy = Arc::new(SpyRag::new());
        let rag: Arc<dyn RagManager> = spy.clone();
        let mgr = PersonalDocsManager::new(&base_path, Some(rag));

        mgr.remove_directory("/never/tracked");

        assert_eq!(spy.rebuilt.load(std::sync::atomic::Ordering::SeqCst), 0);
        assert!(spy.removed.lock().unwrap().is_empty());
    }

    #[test]
    fn add_directory_exclusion_clear_respects_path_boundary() {
        // The exclusion-clear must match on a path BOUNDARY, not a raw prefix:
        // adding `/docs` must NOT un-exclude a file under the sibling `/docs2`.
        let base = tempfile::tempdir().expect("base");
        let base_path = base.path().to_str().unwrap().to_string();
        let mgr = PersonalDocsManager::new(&base_path, None);

        // Two sibling dirs sharing a name prefix: <root>/docs and <root>/docs2.
        let root = base.path();
        let docs = root.join("docs");
        let docs2 = root.join("docs2");
        std::fs::create_dir_all(&docs).unwrap();
        std::fs::create_dir_all(&docs2).unwrap();
        std::fs::write(docs.join("a.txt"), "a").unwrap();
        std::fs::write(docs2.join("b.txt"), "b").unwrap();

        let docs_dir = abspath(docs.to_str().unwrap());
        let file_in_docs = abspath(docs.join("a.txt").to_str().unwrap());
        let file_in_docs2 = abspath(docs2.join("b.txt").to_str().unwrap());

        // Exclude one file in each sibling.
        mgr.exclude_file(&file_in_docs);
        mgr.exclude_file(&file_in_docs2);

        // Adding /docs should clear ONLY the /docs exclusion, leaving /docs2's
        // exclusion intact (the bug being fixed un-excluded /docs2 too).
        mgr.add_directory(&docs_dir, false, None);

        let excluded = mgr.lock().excluded_files.clone();
        assert!(
            !excluded.contains(&file_in_docs),
            "file under the added /docs must be un-excluded"
        );
        assert!(
            excluded.contains(&file_in_docs2),
            "file under sibling /docs2 must remain excluded (path-boundary match)"
        );
    }

    #[test]
    fn add_directory_exclusion_clear_removes_directory_itself() {
        // The directory path itself (p == directory) is cleared too.
        let base = tempfile::tempdir().expect("base");
        let base_path = base.path().to_str().unwrap().to_string();
        let mgr = PersonalDocsManager::new(&base_path, None);

        let target = base.path().join("target");
        std::fs::create_dir_all(&target).unwrap();
        let target_dir = abspath(target.to_str().unwrap());

        // Exclude the directory path itself.
        mgr.exclude_file(&target_dir);
        assert!(mgr.lock().excluded_files.contains(&target_dir));

        mgr.add_directory(&target_dir, false, None);
        assert!(!mgr.lock().excluded_files.contains(&target_dir));
    }

    #[test]
    fn load_personal_index_dispatches_office_to_empty_and_text_to_reader() {
        // Office/EPUB extensions are now indexed (DEFAULT_EXTENSIONS expanded)
        // but route through extract_office_text, which returns "" until a Rust
        // markitdown lands -> 0 chunks. Plain text still reads its content.
        let dir = tempfile::tempdir().expect("tempdir");
        let base = dir.path().to_str().unwrap();
        std::fs::write(dir.path().join("note.txt"), "hello world from text").unwrap();
        // A .docx file (content is irrelevant: office extraction yields "").
        std::fs::write(dir.path().join("report.docx"), "binary-ish docx bytes").unwrap();

        let files = load_personal_index_default(base);
        let by_name: std::collections::HashMap<&str, &IndexedFile> =
            files.iter().map(|f| (f.name.as_str(), f)).collect();

        // The .docx is now picked up (extension is in DEFAULT_EXTENSIONS)...
        let docx = by_name.get("report.docx").expect(".docx must be indexed");
        // ...but its content is empty (office extraction degrades to ""), so it
        // has no chunks.
        assert!(
            docx.chunks.is_empty(),
            "office extraction returns \"\" -> no chunks"
        );

        // Plain text is read normally and chunked.
        let txt = by_name.get("note.txt").expect(".txt must be indexed");
        assert_eq!(txt.chunks, vec!["hello world from text".to_string()]);
    }

    #[test]
    fn default_extensions_include_office_and_pdf() {
        // Parity: DEFAULT_EXTENSIONS expanded to cover pdf + Office/EPUB.
        for ext in [
            ".txt", ".md", ".json", ".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".epub",
        ] {
            assert!(
                DEFAULT_EXTENSIONS.contains(&ext),
                "DEFAULT_EXTENSIONS missing {ext}"
            );
        }
    }

    #[test]
    fn extract_office_text_degrades_to_empty() {
        // No Rust markitdown runtime yet: extract_office_text mirrors the
        // "markitdown unavailable" branch and returns "".
        assert_eq!(extract_office_text("/tmp/whatever.docx"), "");
    }
}
