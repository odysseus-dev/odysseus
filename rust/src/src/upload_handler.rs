// src/upload_handler.rs  <- src/upload_handler.py
//! Upload handling: filename sanitization, upload-id validation, content-type
//! detection, file-type predicates, on-disk save/list/cleanup of uploads.
//!
//! PORT_PARTIAL, `web`-gated. The pure / filesystem logic is a faithful port:
//!   * `secure_filename`            — PORT_NOW. NFKD-normalize, drop non-ASCII
//!                                    (`encode("ascii","ignore")`), replace path
//!                                    separators, `re.sub(r"[^\w\s\-.]","")` +
//!                                    `re.sub(r"[\s]+","_")`, strip leading dots,
//!                                    `or "unnamed"`.
//!   * `validate_upload_id`         — PORT_NOW. Optional-extension id regex
//!                                    (`^[0-9a-fA-F]{32}(?:\.[A-Za-z0-9]+)?$`):
//!                                    a bare 32-hex id (extensionless upload,
//!                                    e.g. `Dockerfile`) is valid too.
//!   * `count_recent_uploads`       — PORT_NOW (free fn). Count upload TIMESTAMPS
//!                                    in the last `window` (10s); does NOT scale
//!                                    with batch size (issue #1346).
//!   * `_build_upload_id`           — PORT_NOW (free fn). `uuid4().hex` + an
//!                                    alphanumeric-sanitized extension, so the
//!                                    id always matches the id regex.
//!   * `get_upload_info` / `find_upload_path` / `inside_upload_dir` /
//!     `resolve_upload`             — PORT_NOW. The owner-scoped resolver surface
//!                                    the doc layer (document_helpers /
//!                                    chat_handler / document_processor /
//!                                    document_routes) reads uploads through.
//!   * `atomic_write_json` / `load_upload_index` — PORT_NOW. uploads.json is
//!                                    written via a temp-file + rename with a
//!                                    `.bak` sidecar, guarded by an index Mutex;
//!                                    the loader falls back to `.bak` on a
//!                                    corrupt live file.
//!   * `is_image_file` / `is_audio_file` / `is_document_file` /
//!     `is_safe_file_type`          — PORT_NOW. Extension + MIME set membership on
//!                                    the lowercased name / content-type.
//!   * `inside_base_dir`            — PORT_NOW. Delegates to the already-faithful
//!                                    `app_helpers::inside_base_dir` (realpath +
//!                                    commonpath), the exact same algorithm the
//!                                    Python `UploadHandler.inside_base_dir` uses.
//!   * `detect_content_type`        — PORT_NOW. `python-magic` libmagic content
//!                                    sniff -> the pure-Rust `infer` crate
//!                                    (`Magic(mime=True).from_buffer(read(1024))`),
//!                                    with the `mimetypes.guess_type` extension
//!                                    fallback when sniffing yields nothing /
//!                                    `application/octet-stream`.
//!   * `calculate_file_hash`        — PORT_NOW. SHA-256 hexdigest of the content.
//!   * `get_upload_dir`             — PORT_NOW. `<upload_dir>/%Y/%m/%d` (LOCAL
//!                                    time, matching `datetime.now()`), created.
//!   * `cleanup_old_uploads`        — PORT_NOW. `os.walk` of date dirs older than
//!                                    `cleanup_days`, removing files + empty dirs.
//!   * `cleanup_rate_limits`        — PORT_NOW. Prune stale timestamps / IPs and
//!                                    cap the dict at `_upload_rate_max_entries`.
//!   * `get_upload_stats`           — PORT_NOW. Read `uploads.json`, aggregate.
//!   * `save_upload`                — PORT_PARTIAL. The rate-limit / size / dedup
//!                                    / write / metadata / `uploads.json` update
//!                                    pipeline is faithful (raises map to
//!                                    `crate::routes::HttpException`). It takes the
//!                                    file *bytes* rather than a FastAPI
//!                                    `UploadFile` (the un-portable web type); the
//!                                    PIL image-dimension capture uses the `image`
//!                                    crate (`exif_transpose` is a documented
//!                                    HONEST simplification — orientation is not
//!                                    applied, see the note at the call site).
//!
//! `UploadHandler` implements [`crate::src::document_processor::UploadHandlerLike`]
//! so the real handler can be threaded through `build_user_content` and
//! `ChatHandler`; the test stubs in those modules stay for unit tests only.


use std::sync::Mutex;

use serde_json::{json, Map, Value};

use crate::core::auth::AuthManager;
use crate::error::PyResult;
use crate::pylog as logger;
use crate::routes::HttpException;
use crate::src::document_processor::UploadHandlerLike;

// ---------------------------------------------------------------------------
// secure_filename  (src/upload_handler.py L13-L27)
// ---------------------------------------------------------------------------

/// Sanitize a filename (replaces werkzeug.utils.secure_filename).
///
/// Faithful port: NFKD normalize, drop non-ASCII (`encode("ascii","ignore")`),
/// replace `/` and `\` (the resolved `os.sep`/`os.altsep` set on every platform
/// the Python targets) with `_`, keep only `[\w\s\-.]`, collapse whitespace runs
/// to `_`, strip leading dots, and fall back to `"unnamed"` when empty.
pub fn secure_filename(filename: &str) -> String {
    use unicode_normalization::UnicodeNormalization;

    // unicodedata.normalize("NFKD", filename)
    let normalized: String = filename.nfkd().collect();
    // .encode("ascii", "ignore").decode("ascii") — keep only ASCII code points.
    let ascii_only: String = normalized.chars().filter(|c| c.is_ascii()).collect();

    // for sep in (os.sep, os.altsep or "", "/", "\\"): replace sep -> "_"
    // (os.sep is "/" on Unix and "\\" on Windows; os.altsep is None on Unix and
    // "/" on Windows. The literal "/" and "\\" entries cover both, so the net
    // effect on every platform is: replace both "/" and "\\" with "_".)
    let replaced: String = ascii_only.replace(['/', '\\'], "_");

    // re.sub(r"[^\w\s\-.]", "", filename).strip()
    // `\w` is [A-Za-z0-9_] (input is ASCII-only by this point); `\s` is
    // whitespace. Drop everything else, then strip leading/trailing whitespace.
    let kept: String = replaced
        .chars()
        .filter(|c| c.is_alphanumeric() || *c == '_' || c.is_whitespace() || *c == '-' || *c == '.')
        .collect();
    let stripped = kept.trim();

    // re.sub(r"[\s]+", "_", filename) — collapse whitespace runs to a single "_".
    let mut underscored = String::with_capacity(stripped.len());
    let mut in_ws = false;
    for c in stripped.chars() {
        if c.is_whitespace() {
            if !in_ws {
                underscored.push('_');
                in_ws = true;
            }
        } else {
            underscored.push(c);
            in_ws = false;
        }
    }

    // filename.lstrip(".")
    let no_dot = underscored.trim_start_matches('.');

    // return filename or "unnamed"
    if no_dot.is_empty() {
        "unnamed".to_string()
    } else {
        no_dot.to_string()
    }
}

// ---------------------------------------------------------------------------
// _build_upload_id  (src/upload_handler.py L46-L57)
// ---------------------------------------------------------------------------

/// Build a unique upload id whose extension matches `UPLOAD_ID_RE`.
///
/// `secure_filename` keeps `_` and `-`, so an extension like `.jpg-1` (the
/// suffix browsers append to duplicate downloads) or `.v1_final` produced an id
/// that failed `is_valid_upload_id`, making the saved file permanently
/// unreadable (every read path gates on `validate_upload_id`). Sanitize the
/// extension to the single-alnum shape the id contract requires:
/// `re.sub(r"[^A-Za-z0-9]", "", ext)`.
fn build_upload_id(safe_filename: &str) -> String {
    // _, ext = os.path.splitext(safe_filename or "")
    let ext = splitext_ext(safe_filename); // includes the leading '.', or ""
    // ext = re.sub(r"[^A-Za-z0-9]", "", ext) — strip the dot and any non-alnum.
    let ext_alnum: String = ext.chars().filter(|c| c.is_ascii_alphanumeric()).collect();
    // return uuid4().hex + (("." + ext) if ext else "")
    let uuid_hex = uuid::Uuid::new_v4().simple().to_string();
    if ext_alnum.is_empty() {
        uuid_hex
    } else {
        format!("{uuid_hex}.{ext_alnum}")
    }
}

// ---------------------------------------------------------------------------
// count_recent_uploads  (src/upload_handler.py L60-L69)
// ---------------------------------------------------------------------------

/// Number of upload events in `timestamps` within the last `window` seconds.
///
/// Used by the per-IP concurrency guard. The count is of genuine prior upload
/// events — it must NOT scale with how many files are in the *current* request,
/// or a single multi-file batch would reject itself (issue #1346).
///
/// Faithful port of the Python free function `count_recent_uploads(timestamps,
/// now, window=10.0)`.
pub fn count_recent_uploads(timestamps: &[f64], now: f64, window: f64) -> usize {
    // if not timestamps: return 0
    if timestamps.is_empty() {
        return 0;
    }
    // cutoff = now - window; sum(1 for t in timestamps if t > cutoff)
    let cutoff = now - window;
    timestamps.iter().filter(|&&t| t > cutoff).count()
}

// ---------------------------------------------------------------------------
// UploadHandler  (src/upload_handler.py L72-L110)
// ---------------------------------------------------------------------------

/// Stateful upload handler. Mirrors the Python `UploadHandler` instance.
pub struct UploadHandler {
    base_dir: String,
    upload_dir: String,
    max_upload_size: u64,
    max_concurrent_uploads: u32,
    cleanup_days: i64,
    upload_rate_limit: usize,
    upload_rate_window: f64,

    // Rate-limit bookkeeping (the Python `threading.Lock`-guarded dict + counter).
    rate: Mutex<RateState>,
    upload_rate_max_entries: usize,

    // `self._index_lock` — serialises the read-modify-write of uploads.json
    // within one process (the Python `threading.Lock`). The atomic temp-write +
    // rename keeps the on-disk file consistent on its own; this lock serialises
    // writers so a duplicate insert racing a new-entry insert cannot clobber a
    // newer snapshot with a stale one. The guard holds no data; it exists only
    // for its critical section.
    index_lock: Mutex<()>,

    // `self.file_detector` — the libmagic detector. The Rust port uses the
    // pure-Rust `infer` crate (always available), so this flag is always true.
    file_detector: bool,
}

/// The lock-guarded mutable state (`upload_rate_log` + `_upload_rate_counter`).
struct RateState {
    /// `Dict[str, list]` — per-IP list of upload timestamps. Insertion order is
    /// preserved (Python dict) so the `> _upload_rate_max_entries` eviction has
    /// the same tie-break ordering.
    upload_rate_log: indexmap::IndexMap<String, Vec<f64>>,
    upload_rate_counter: u64,
}

impl UploadHandler {
    /// `UploadHandler(base_dir, upload_dir)`.
    ///
    /// Creates the upload directory (`os.makedirs(..., exist_ok=True)`).
    pub fn new(base_dir: &str, upload_dir: &str) -> Self {
        // os.makedirs(self.upload_dir, exist_ok=True)
        if let Err(e) = crate::pyos::makedirs(upload_dir, true) {
            // Python lets a makedirs failure propagate; mirror best-effort and
            // log, since this is a constructor (Python would raise here, but the
            // callers always pass a writable dir).
            logger::warning(&format!("Failed to create upload dir {upload_dir}: {e}"));
        }

        // try: import magic; self.file_detector = magic.Magic(mime=True)
        // except Exception: self.file_detector = None  ... -> the `infer` crate
        // is pure-Rust and always present, so detection is always available.
        UploadHandler {
            base_dir: base_dir.to_string(),
            upload_dir: upload_dir.to_string(),
            max_upload_size: 10 * 1024 * 1024, // 10MB
            max_concurrent_uploads: 3,
            cleanup_days: 30,
            // Per-IP per-minute cap. save_upload() counts EACH file, and the
            // chat composer lets a user attach up to MAX_FILES (10) in one
            // batch, so this must comfortably exceed 10 or a single 6+ file
            // attach is rejected mid-batch (issue #1346: "5 work, 6 fail").
            // Burst abuse is separately bounded by max_concurrent_uploads.
            upload_rate_limit: 60,
            upload_rate_window: 60.0,
            rate: Mutex::new(RateState {
                upload_rate_log: indexmap::IndexMap::new(),
                upload_rate_counter: 0,
            }),
            upload_rate_max_entries: 1000,
            index_lock: Mutex::new(()),
            file_detector: true,
        }
    }

    /// `self.upload_dir`.
    pub fn upload_dir(&self) -> &str {
        &self.upload_dir
    }

    /// `self.base_dir`.
    pub fn base_dir(&self) -> &str {
        &self.base_dir
    }

    /// `self.max_concurrent_uploads` — the per-IP concurrent-upload cap (3).
    ///
    /// Exposed for the `routes/upload_routes.py::api_upload` per-IP burst check,
    /// which reads `upload_handler.max_concurrent_uploads` directly off the
    /// instance. Mirrors that attribute access.
    pub fn max_concurrent_uploads(&self) -> u32 {
        self.max_concurrent_uploads
    }

    /// `count_recent_uploads(upload_rate_log.get(client_ip, []), time.time())`
    /// from `routes/upload_routes.py::api_upload` — the per-IP concurrent-upload
    /// burst count.
    ///
    /// issue #1346: the previous `count_concurrent_uploads(client_ip, n_files)`
    /// summed over `files`, so a single multi-file request counted itself as
    /// `n_files` concurrent uploads and tripped the `max_concurrent_uploads`
    /// limit. The fix counts genuine recent upload TIMESTAMPS in the last 10
    /// seconds (independent of batch size). The route reads
    /// `upload_handler.upload_rate_log.get(client_ip, [])`; that dict is private
    /// behind the rate `Mutex` here, so the lookup + count happen on the handler
    /// (taking the same lock so the read is consistent with concurrent
    /// `save_upload` writes) and delegate to the free [`count_recent_uploads`].
    pub fn count_recent_uploads(&self, client_ip: &str) -> usize {
        // upload_handler.upload_rate_log.get(client_ip, [])
        let rate = self.rate.lock().unwrap();
        let timestamps = match rate.upload_rate_log.get(client_ip) {
            Some(ts) => ts.as_slice(),
            None => &[][..],
        };
        // count_recent_uploads(timestamps, time.time())  (window defaults to 10s)
        let now = crate::pytime::time();
        count_recent_uploads(timestamps, now, 10.0)
    }

    // -----------------------------------------------------------------------
    // inside_base_dir  (L59-L66)
    // -----------------------------------------------------------------------

    /// Check if path is inside base directory.
    ///
    /// Same algorithm as Python (`os.path.realpath` + `os.path.commonpath`),
    /// already faithfully implemented in `app_helpers::inside_base_dir`.
    pub fn inside_base_dir(&self, path: &str) -> bool {
        crate::src::app_helpers::inside_base_dir(&self.base_dir, path)
    }

    // -----------------------------------------------------------------------
    // get_upload_dir  (L68-L73)
    // -----------------------------------------------------------------------

    /// Get date-based upload directory (`<upload_dir>/%Y/%m/%d`), created.
    ///
    /// Uses LOCAL time to match Python's `datetime.now()`.
    pub fn get_upload_dir(&self) -> String {
        use chrono::{Datelike, Local};
        let now = Local::now();
        // os.path.join(self.upload_dir, "%Y", "%m", "%d") — zero-padded.
        let year = format!("{:04}", now.year());
        let month = format!("{:02}", now.month());
        let day = format!("{:02}", now.day());
        let upload_dir = crate::pyos::path::join(
            &crate::pyos::path::join(&crate::pyos::path::join(&self.upload_dir, &year), &month),
            &day,
        );
        let _ = crate::pyos::makedirs(&upload_dir, true);
        upload_dir
    }

    // -----------------------------------------------------------------------
    // calculate_file_hash  (L75-L82)
    // -----------------------------------------------------------------------

    /// Calculate SHA-256 hash (hexdigest) of file content.
    ///
    /// Python streams the file in 4096-byte chunks; the resulting digest of the
    /// whole content is identical to hashing the bytes in one pass.
    pub fn calculate_file_hash(&self, content: &[u8]) -> String {
        use sha2::{Digest, Sha256};
        let mut hasher = Sha256::new();
        hasher.update(content);
        hex::encode(hasher.finalize())
    }

    // -----------------------------------------------------------------------
    // detect_content_type  (L84-L100)
    // -----------------------------------------------------------------------

    /// Detect MIME type based on file content, with extension fallback.
    ///
    /// Mirrors `Magic(mime=True).from_buffer(file_obj.read(1024))` with the
    /// pure-Rust `infer` crate (sniffs the leading bytes), then the
    /// `mimetypes.guess_type` extension fallback when the sniff yields nothing or
    /// `application/octet-stream`.
    pub fn detect_content_type(&self, content: &[u8], original_filename: &str) -> String {
        let mut content_type = "application/octet-stream".to_string();
        if self.file_detector {
            // file_obj.read(1024) — sniff the leading 1024 bytes.
            let head = &content[..content.len().min(1024)];
            if let Some(kind) = infer::get(head) {
                content_type = kind.mime_type().to_string();
            }
            // (an `infer` miss leaves the octet-stream default, matching the
            //  Python try/except which keeps the prior value on failure.)
        }

        // if not content_type or content_type == "application/octet-stream":
        if content_type.is_empty() || content_type == "application/octet-stream" {
            // _, ext = os.path.splitext(original_filename.lower())
            let lowered = original_filename.to_lowercase();
            let ext = splitext_ext(&lowered);
            if !ext.is_empty() {
                // mimetypes.guess_type(original_filename)[0] or content_type
                if let Some(guessed) = mime_guess::from_path(original_filename).first_raw() {
                    content_type = guessed.to_string();
                }
            }
        }

        content_type
    }

    // -----------------------------------------------------------------------
    // is_image_file / is_document_file / is_audio_file  (L102-L161)
    // -----------------------------------------------------------------------

    /// Check if a file is an image based on extension or content type.
    pub fn is_image_file(&self, filename: &str, content_type: Option<&str>) -> bool {
        const IMAGE_EXTENSIONS: &[&str] = &[".png", ".jpg", ".jpeg", ".webp", ".gif"];
        const IMAGE_MIME_TYPES: &[&str] = &[
            "image/png",
            "image/jpeg",
            "image/jpg",
            "image/webp",
            "image/gif",
        ];

        let ext = splitext_ext(&filename.to_lowercase());
        if IMAGE_EXTENSIONS.contains(&ext.as_str()) {
            return true;
        }
        if let Some(ct) = content_type {
            if IMAGE_MIME_TYPES.contains(&ct) {
                return true;
            }
        }
        false
    }

    /// Check if a file is a document based on extension or content type.
    pub fn is_document_file(&self, filename: &str, content_type: Option<&str>) -> bool {
        const DOCUMENT_EXTENSIONS: &[&str] = &[
            ".pdf", ".docx", ".xlsx", ".pptx", ".xls", ".epub", ".txt", ".py", ".js", ".html",
            ".htm", ".css", ".json", ".md", ".csv", ".log", ".xml", ".yml", ".yaml", ".sql", ".sh",
            ".bash", ".c", ".cpp", ".h", ".java", ".go", ".rs", ".php", ".rb", ".ts", ".jsx",
            ".tsx",
        ];
        const DOCUMENT_MIME_TYPES: &[&str] = &[
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/vnd.ms-excel",
            "application/epub+zip",
            "text/plain",
        ];

        let ext = splitext_ext(&filename.to_lowercase());
        if DOCUMENT_EXTENSIONS.contains(&ext.as_str()) {
            return true;
        }
        if let Some(ct) = content_type {
            if DOCUMENT_MIME_TYPES.contains(&ct) {
                return true;
            }
        }
        false
    }

    /// Check if a file is an audio file based on extension or content type.
    pub fn is_audio_file(&self, filename: &str, content_type: Option<&str>) -> bool {
        const AUDIO_EXTENSIONS: &[&str] = &[".webm", ".wav", ".mp3", ".m4a", ".ogg"];
        const AUDIO_MIME_TYPES: &[&str] = &[
            "audio/webm",
            "audio/wav",
            "audio/mpeg",
            "audio/mp4",
            "audio/ogg",
        ];

        let ext = splitext_ext(&filename.to_lowercase());
        if AUDIO_EXTENSIONS.contains(&ext.as_str()) {
            return true;
        }
        if let Some(ct) = content_type {
            if AUDIO_MIME_TYPES.contains(&ct) {
                return true;
            }
        }
        false
    }

    // -----------------------------------------------------------------------
    // is_safe_file_type  (L163-L184)
    // -----------------------------------------------------------------------

    /// Check if file type is safe to store and serve.
    pub fn is_safe_file_type(&self, content_type: &str, filename: &str) -> bool {
        const DANGEROUS_TYPES: &[&str] = &[
            "application/x-executable",
            "application/x-sharedlib",
            "application/x-dll",
            "application/x-msdownload",
            "application/x-sh",
            "application/x-bat",
            "application/x-vbs",
            "application/javascript",
            "application/x-javascript",
        ];
        const DANGEROUS_EXTENSIONS: &[&str] = &[
            ".exe", ".dll", ".bat", ".cmd", ".vbs", ".ps1", ".jsp", ".asp", ".aspx",
        ];

        if DANGEROUS_TYPES.contains(&content_type) {
            return false;
        }
        let ext = splitext_ext(&filename.to_lowercase());
        if DANGEROUS_EXTENSIONS.contains(&ext.as_str()) {
            return false;
        }
        true
    }

    // -----------------------------------------------------------------------
    // cleanup_old_uploads  (L186-L222)
    // -----------------------------------------------------------------------

    /// Remove uploaded files older than `cleanup_days` days.
    ///
    /// Returns the number of files removed (0 on outer failure, matching the
    /// Python `except Exception: ... return 0`).
    pub fn cleanup_old_uploads(&self) -> i64 {
        use chrono::{Duration, Local, NaiveDate};

        // cutoff_date = datetime.now() - timedelta(days=self.cleanup_days)
        let cutoff_date = Local::now().naive_local() - Duration::days(self.cleanup_days);
        let mut cleaned_count: i64 = 0;

        // os.walk(self.upload_dir): visit every directory.
        for entry in walkdir::WalkDir::new(&self.upload_dir)
            .into_iter()
            .filter_map(|e| e.ok())
        {
            if !entry.file_type().is_dir() {
                continue;
            }
            let root = entry.path().to_string_lossy().into_owned();
            // if root == self.upload_dir: continue
            if root == self.upload_dir {
                continue;
            }

            // path_parts = root.split(os.sep)
            let path_parts: Vec<&str> = root.split(std::path::MAIN_SEPARATOR).collect();
            if path_parts.len() >= 4 {
                let n = path_parts.len();
                // datetime(int(parts[-3]), int(parts[-2]), int(parts[-1]))
                let y = path_parts[n - 3].parse::<i32>();
                let m = path_parts[n - 2].parse::<u32>();
                let d = path_parts[n - 1].parse::<u32>();
                let dir_date = match (y, m, d) {
                    (Ok(y), Ok(m), Ok(d)) => NaiveDate::from_ymd_opt(y, m, d),
                    // except (ValueError, IndexError): continue
                    _ => continue,
                };
                let dir_date = match dir_date.and_then(|nd| nd.and_hms_opt(0, 0, 0)) {
                    Some(dt) => dt,
                    None => continue, // invalid date -> Python ValueError -> continue
                };

                if dir_date < cutoff_date {
                    // Only the immediate files of this dir (os.walk's `files`).
                    if let Ok(read_dir) = std::fs::read_dir(&root) {
                        for file_entry in read_dir.filter_map(|e| e.ok()) {
                            if file_entry.file_type().map(|t| t.is_file()).unwrap_or(false) {
                                let file_path = file_entry.path();
                                match std::fs::remove_file(&file_path) {
                                    Ok(()) => {
                                        cleaned_count += 1;
                                        logger::info(&format!(
                                            "Cleaned up old upload: {}",
                                            file_path.display()
                                        ));
                                    }
                                    Err(e) => logger::warning(&format!(
                                        "Failed to remove {}: {e}",
                                        file_path.display()
                                    )),
                                }
                            }
                        }
                    }
                    // os.rmdir(root) — remove the now-empty directory.
                    match std::fs::remove_dir(&root) {
                        Ok(()) => logger::info(&format!("Removed empty upload directory: {root}")),
                        Err(e) => {
                            logger::warning(&format!("Failed to remove directory {root}: {e}"))
                        }
                    }
                }
            }
        }

        logger::info(&format!(
            "Upload cleanup completed: {cleaned_count} files removed"
        ));
        cleaned_count
    }

    // -----------------------------------------------------------------------
    // validate_upload_id  (L224-L227)
    // -----------------------------------------------------------------------

    /// Validate that the upload ID matches the expected pattern.
    pub fn validate_upload_id(&self, upload_id: &str) -> bool {
        validate_upload_id(upload_id)
    }

    // -----------------------------------------------------------------------
    // _inside_upload_dir  (L286-L293)
    // -----------------------------------------------------------------------

    /// Check if path is inside the upload directory.
    ///
    /// Same algorithm as Python (`os.path.realpath` + `os.path.commonpath`) —
    /// identical to `inside_base_dir` but rooted at `self.upload_dir`. Reuses the
    /// already-faithful `app_helpers::inside_base_dir`, which is exactly the
    /// realpath + commonpath containment check `_inside_upload_dir` performs.
    pub fn inside_upload_dir(&self, path: &str) -> bool {
        crate::src::app_helpers::inside_base_dir(&self.upload_dir, path)
    }

    // -----------------------------------------------------------------------
    // _atomic_write_json  (L295-L321)
    // -----------------------------------------------------------------------

    /// Write `data` to `path` atomically: write to a temp file in the same
    /// directory, fsync, then rename onto the target. `std::fs::rename` is atomic
    /// on POSIX, so a reader sees either the old contents or the new contents,
    /// never a half-written file. Also keeps a `.bak` sibling of the previous
    /// good state (`shutil.copy2` before the rename).
    ///
    /// (The Python uses `tempfile.mkstemp`; `tempfile` is a dev-only dependency
    /// here, so the temp file is created with a unique name via `create_new` in
    /// the same directory — same atomicity guarantee.)
    fn atomic_write_json(&self, path: &str, data: &Value) -> std::io::Result<()> {
        use std::io::Write;

        // directory = os.path.dirname(path) or "."
        let directory = {
            let d = crate::pyos::path::dirname(path);
            if d.is_empty() { ".".to_string() } else { d }
        };

        // tempfile.mkstemp(prefix=".uploads-", suffix=".tmp", dir=directory)
        let tmp = {
            let mut chosen = None;
            for _ in 0..16 {
                let candidate = crate::pyos::path::join(
                    &directory,
                    &format!(".uploads-{}.tmp", uuid::Uuid::new_v4().simple()),
                );
                match std::fs::OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&candidate)
                {
                    Ok(file) => {
                        chosen = Some((candidate, file));
                        break;
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
                    Err(e) => return Err(e),
                }
            }
            chosen.ok_or_else(|| {
                std::io::Error::new(
                    std::io::ErrorKind::AlreadyExists,
                    "could not create a unique temp file for uploads.json",
                )
            })?
        };
        let (tmp_path, mut tmp_file) = tmp;

        let write_result = (|| -> std::io::Result<()> {
            // json.dump(data, f, indent=2); f.flush(); os.fsync(f.fileno())
            let s = serde_json::to_string_pretty(data).map_err(std::io::Error::other)?;
            tmp_file.write_all(s.as_bytes())?;
            tmp_file.flush()?;
            tmp_file.sync_all()?;
            drop(tmp_file);

            // if os.path.exists(path): shutil.copy2(path, path + ".bak")
            if crate::pyos::path::exists(path) {
                let bak = format!("{path}.bak");
                // OSError on the copy is swallowed in Python (try/except pass).
                let _ = std::fs::copy(path, &bak);
            }
            // os.replace(tmp, path)
            std::fs::rename(&tmp_path, path)
        })();

        if write_result.is_err() {
            // except Exception: try: os.unlink(tmp) except OSError: pass; raise
            let _ = std::fs::remove_file(&tmp_path);
        }
        write_result
    }

    // -----------------------------------------------------------------------
    // _load_upload_index  (L323-L340)
    // -----------------------------------------------------------------------

    /// Load the `uploads.json` index. Tries the live file first, then falls back
    /// to the `.bak` sibling if the live file is truncated/corrupted. Returns an
    /// empty map when neither parses to a JSON object.
    fn load_upload_index(&self) -> Map<String, Value> {
        let uploads_db_path = crate::pyos::path::join(&self.upload_dir, "uploads.json");
        if !crate::pyos::path::exists(&uploads_db_path) {
            return Map::new();
        }
        let bak = format!("{uploads_db_path}.bak");
        for candidate in [uploads_db_path.as_str(), bak.as_str()] {
            if !crate::pyos::path::exists(candidate) {
                continue;
            }
            match std::fs::read_to_string(candidate)
                .ok()
                .and_then(|s| serde_json::from_str::<Value>(&s).ok())
            {
                // return data if isinstance(data, dict) else {}
                Some(Value::Object(o)) => return o,
                Some(_) => return Map::new(),
                None => {
                    logger::warning(&format!(
                        "Failed to read uploads database ({candidate})"
                    ));
                    continue;
                }
            }
        }
        Map::new()
    }

    // -----------------------------------------------------------------------
    // get_upload_info  (L342-L349)
    // -----------------------------------------------------------------------

    /// Return the `uploads.json` metadata row for an upload ID, if present.
    pub fn get_upload_info(&self, upload_id: &str) -> Option<Map<String, Value>> {
        if !self.validate_upload_id(upload_id) {
            return None;
        }
        for info in self.load_upload_index().values() {
            if let Some(obj) = info.as_object() {
                if obj.get("id").and_then(|v| v.as_str()) == Some(upload_id) {
                    return Some(obj.clone());
                }
            }
        }
        None
    }

    // -----------------------------------------------------------------------
    // _find_upload_path  (L351-L365)
    // -----------------------------------------------------------------------

    /// Find an upload file by ID while staying inside `upload_dir`.
    pub fn find_upload_path(&self, upload_id: &str) -> Option<String> {
        if !self.validate_upload_id(upload_id) {
            return None;
        }

        // direct = os.path.join(self.upload_dir, upload_id)
        let direct = crate::pyos::path::join(&self.upload_dir, upload_id);
        if crate::pyos::path::exists(&direct) && self.inside_upload_dir(&direct) {
            return Some(direct);
        }

        // for root, _dirs, files in os.walk(self.upload_dir, followlinks=False):
        //     if upload_id in files: ...
        for entry in walkdir::WalkDir::new(&self.upload_dir)
            .follow_links(false)
            .into_iter()
            .filter_map(|e| e.ok())
        {
            if entry.file_type().is_file()
                && entry.file_name().to_string_lossy() == upload_id
            {
                let path = entry.path().to_string_lossy().into_owned();
                if self.inside_upload_dir(&path) {
                    return Some(path);
                }
            }
        }
        None
    }

    // -----------------------------------------------------------------------
    // resolve_upload  (L367-L419)
    // -----------------------------------------------------------------------

    /// Resolve an upload ID to metadata only if the caller may read it.
    ///
    /// This is the owner-aware lookup used by internal processors (chat /
    /// document paths). Public download routes already perform owner checks;
    /// chat/document paths must do the same before reading file bytes
    /// server-side. Faithful port of the Python `resolve_upload(upload_id,
    /// owner=None, auth_manager=None, allow_admin=True)`.
    pub fn resolve_upload(
        &self,
        upload_id: &str,
        owner: Option<&str>,
        auth_manager: Option<&AuthManager>,
        allow_admin: bool,
    ) -> Option<Map<String, Value>> {
        // if not self.validate_upload_id(upload_id): warn; return None
        if !self.validate_upload_id(upload_id) {
            logger::warning(&format!("Invalid upload ID format: {upload_id}"));
            return None;
        }

        // auth_configured = bool(auth_manager and getattr(am, "is_configured", False))
        let auth_configured = auth_manager.map(|am| am.is_configured()).unwrap_or(false);
        // if auth_configured and not owner: return None
        if auth_configured && owner.is_none() {
            return None;
        }

        // info = self.get_upload_info(upload_id) or {}
        let info = self.get_upload_info(upload_id).unwrap_or_default();

        // is_admin = bool(auth_manager.is_admin(owner)) when allow_admin & owner & am
        let mut is_admin = false;
        if allow_admin {
            if let (Some(owner), Some(am)) = (owner, auth_manager) {
                is_admin = am.is_admin(owner);
            }
        }

        // if owner and not is_admin: deny unless info["owner"] == owner
        if let Some(owner) = owner {
            if !is_admin && info.get("owner").and_then(|v| v.as_str()) != Some(owner) {
                logger::warning(&format!("Upload {upload_id} denied for owner {owner}"));
                return None;
            }
        }
        // if not owner and info.get("owner") is not None: deny
        if owner.is_none()
            && info.get("owner").map(|v| !v.is_null()).unwrap_or(false)
        {
            logger::warning(&format!(
                "Upload {upload_id} denied without an authenticated owner"
            ));
            return None;
        }

        // path = info.get("path"); fall back to _find_upload_path when missing /
        // nonexistent / outside the upload dir.
        let info_path = info.get("path").and_then(|v| v.as_str()).map(str::to_string);
        let path = match info_path {
            Some(p) if crate::pyos::path::exists(&p) && self.inside_upload_dir(&p) => Some(p),
            _ => self.find_upload_path(upload_id),
        };
        let path = path?;
        if !self.inside_upload_dir(&path) {
            logger::warning(&format!("Upload path outside upload directory: {path}"));
            return None;
        }

        // resolved = dict(info); resolved.setdefault(...); resolved["path"] = path
        let mut resolved = info;
        resolved
            .entry("id".to_string())
            .or_insert_with(|| Value::String(upload_id.to_string()));
        resolved.insert("path".to_string(), Value::String(path.clone()));
        let basename = crate::pyos::path::basename(&path);
        resolved
            .entry("name".to_string())
            .or_insert_with(|| Value::String(basename.clone()));
        let name_default = resolved
            .get("name")
            .cloned()
            .unwrap_or_else(|| Value::String(basename.clone()));
        resolved
            .entry("original_name".to_string())
            .or_insert(name_default);
        // resolved.setdefault("mime", mimetypes.guess_type(path)[0] or octet-stream)
        let mime_default = mime_guess::from_path(&path)
            .first_raw()
            .unwrap_or("application/octet-stream")
            .to_string();
        resolved
            .entry("mime".to_string())
            .or_insert_with(|| Value::String(mime_default));
        Some(resolved)
    }

    // -----------------------------------------------------------------------
    // cleanup_rate_limits  (L229-L261)
    // -----------------------------------------------------------------------

    /// Remove stale entries from `upload_rate_log`.
    pub fn cleanup_rate_limits(&self) {
        let now = crate::pytime::time();
        let mut removed_ips = 0u64;
        let mut removed_timestamps = 0u64;

        {
            let mut rate = self.rate.lock().unwrap();
            let mut ips_to_delete: Vec<String> = Vec::new();

            // for ip, timestamps in list(self.upload_rate_log.items()):
            let keys: Vec<String> = rate.upload_rate_log.keys().cloned().collect();
            for ip in &keys {
                let timestamps = rate.upload_rate_log.get(ip).cloned().unwrap_or_default();
                let new_ts: Vec<f64> = timestamps
                    .iter()
                    .copied()
                    .filter(|t| now - t < self.upload_rate_window)
                    .collect();
                let removed = timestamps.len() - new_ts.len();
                removed_timestamps += removed as u64;
                if !new_ts.is_empty() {
                    rate.upload_rate_log.insert(ip.clone(), new_ts);
                } else {
                    ips_to_delete.push(ip.clone());
                }
            }

            for ip in &ips_to_delete {
                // del self.upload_rate_log[ip]
                rate.upload_rate_log.shift_remove(ip);
                removed_ips += 1;
            }

            if rate.upload_rate_log.len() > self.upload_rate_max_entries {
                // sorted(items, key=lambda i: max(i[1]) if i[1] else 0, reverse=True)
                let mut items: Vec<(String, Vec<f64>)> = rate
                    .upload_rate_log
                    .iter()
                    .map(|(k, v)| (k.clone(), v.clone()))
                    .collect();
                // Python's `sorted` is stable; reverse=True with a key reverses
                // the comparison while preserving the original order for ties.
                items.sort_by(|a, b| {
                    let ka = a.1.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
                    let ka = if a.1.is_empty() { 0.0 } else { ka };
                    let kb = b.1.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
                    let kb = if b.1.is_empty() { 0.0 } else { kb };
                    // reverse=True: descending by key, stable on ties.
                    kb.partial_cmp(&ka).unwrap_or(std::cmp::Ordering::Equal)
                });
                let keep_n = self.upload_rate_max_entries;
                let dropped = rate.upload_rate_log.len() - keep_n.min(items.len());
                let mut keep = indexmap::IndexMap::new();
                for (k, v) in items.into_iter().take(keep_n) {
                    keep.insert(k, v);
                }
                rate.upload_rate_log = keep;
                logger::info(&format!(
                    "Rate-limit dict size exceeded. Dropped {dropped} oldest IP entries."
                ));
            }
        }

        logger::info(&format!(
            "Rate-limit cleanup: removed {removed_ips} IPs, {removed_timestamps} timestamps."
        ));
    }

    // -----------------------------------------------------------------------
    // get_upload_stats  (L263-L290)
    // -----------------------------------------------------------------------

    /// Get statistics about uploaded files.
    ///
    /// Returns the `{total_files, total_size, total_size_mb, file_types,
    /// cleanup_days}` object, or `{"error": <msg>}` on failure.
    pub fn get_upload_stats(&self) -> Map<String, Value> {
        match self.get_upload_stats_inner() {
            Ok(m) => m,
            Err(e) => {
                logger::error(&format!("Failed to get upload stats: {e}"));
                let mut out = Map::new();
                out.insert("error".to_string(), Value::String(e.to_string()));
                out
            }
        }
    }

    fn get_upload_stats_inner(&self) -> PyResult<Map<String, Value>> {
        let mut total_files: u64 = 0;
        let mut total_size: i64 = 0;
        // file_types: insertion-ordered (Python dict).
        let mut file_types: indexmap::IndexMap<String, i64> = indexmap::IndexMap::new();

        let uploads_db_path = crate::pyos::path::join(&self.upload_dir, "uploads.json");
        if crate::pyos::path::exists(&uploads_db_path) {
            let raw = std::fs::read_to_string(&uploads_db_path)?;
            let files: Value = serde_json::from_str(&raw)?;
            if let Some(obj) = files.as_object() {
                total_files = obj.len() as u64;
                for file_info in obj.values() {
                    // total_size += file_info.get("size", 0)
                    total_size += file_info.get("size").and_then(|v| v.as_i64()).unwrap_or(0);
                    // mime = file_info.get("mime", "unknown")
                    let mime = file_info
                        .get("mime")
                        .and_then(|v| v.as_str())
                        .unwrap_or("unknown")
                        .to_string();
                    *file_types.entry(mime).or_insert(0) += 1;
                }
            }
        }

        // total_size_mb = round(total_size / (1024 * 1024), 2)
        let total_size_mb = round2(total_size as f64 / (1024.0 * 1024.0));

        let mut file_types_obj = Map::new();
        for (k, v) in file_types {
            file_types_obj.insert(k, json!(v));
        }

        let mut out = Map::new();
        out.insert("total_files".to_string(), json!(total_files));
        out.insert("total_size".to_string(), json!(total_size));
        out.insert("total_size_mb".to_string(), json!(total_size_mb));
        out.insert("file_types".to_string(), Value::Object(file_types_obj));
        out.insert("cleanup_days".to_string(), json!(self.cleanup_days));
        Ok(out)
    }

    // -----------------------------------------------------------------------
    // save_upload  (L292-L459)
    // -----------------------------------------------------------------------

    /// Save uploaded file with enhanced security and organization.
    ///
    /// PORT_PARTIAL. Takes the file *bytes* + the original filename rather than a
    /// FastAPI `UploadFile` (the web-framework type is not portable here); the
    /// rate-limit / size / dedup / write / metadata / `uploads.json` update
    /// pipeline is otherwise faithful. Raises map to
    /// `Err(crate::routes::HttpException { status_code, detail })`.
    pub fn save_upload(
        &self,
        content: &[u8],
        filename: Option<&str>,
        client_ip: &str,
        owner: Option<&str>,
    ) -> Result<Map<String, Value>, HttpException> {
        // --- Rate limiting (the `with self._upload_rate_lock:` block) ---
        let now = crate::pytime::time();
        let do_cleanup;
        {
            let mut rate = self.rate.lock().unwrap();
            // if client_ip not in self.upload_rate_log: self.upload_rate_log[ip]=[]
            rate.upload_rate_log
                .entry(client_ip.to_string())
                .or_default();
            // filter the window.
            let filtered: Vec<f64> = rate.upload_rate_log[client_ip]
                .iter()
                .copied()
                .filter(|t| now - t < self.upload_rate_window)
                .collect();
            rate.upload_rate_log
                .insert(client_ip.to_string(), filtered);

            if rate.upload_rate_log[client_ip].len() >= self.upload_rate_limit {
                return Err(HttpException::new(
                    429,
                    "Upload rate limit exceeded. Please try again later.",
                ));
            }

            rate.upload_rate_log
                .get_mut(client_ip)
                .unwrap()
                .push(now);
            rate.upload_rate_counter += 1;
            do_cleanup = rate.upload_rate_counter.is_multiple_of(100);
        }
        if do_cleanup {
            self.cleanup_rate_limits();
        }

        // --- Validate file size ---
        let file_size = content.len() as u64;
        if file_size == 0 {
            return Err(HttpException::new(400, "File is empty"));
        }
        if file_size > self.max_upload_size {
            // f"File size exceeds {self.max_upload_size/1024/1024}MB limit"
            // Python true-divides: 10*1024*1024/1024/1024 == 10.0 (a float).
            let limit_mb = self.max_upload_size as f64 / 1024.0 / 1024.0;
            return Err(HttpException::new(
                400,
                format!("File size exceeds {}MB limit", py_float_repr(limit_mb)),
            ));
        }

        // original_filename = u.filename or f"upload_{int(time.time())}"
        let original_filename = match filename {
            Some(f) if !f.is_empty() => f.to_string(),
            _ => format!("upload_{}", now as i64),
        };
        let safe_filename = secure_filename(&original_filename);

        // content_type = self.detect_content_type(file_obj, safe_filename)
        let content_type = self.detect_content_type(content, &safe_filename);

        // if not self.is_safe_file_type(content_type, safe_filename):
        if !self.is_safe_file_type(&content_type, &safe_filename) {
            return Err(HttpException::new(
                400,
                format!("File type not allowed: {content_type}"),
            ));
        }

        // file_hash = self.calculate_file_hash(file_obj)
        let file_hash = self.calculate_file_hash(content);

        // --- Duplicate check against uploads.json (owner-scoped) ---
        //
        // The duplicate-detection lookup AND the write both happen under
        // `index_lock`: a duplicate upload racing with a new-entry insert must
        // not overwrite a newer snapshot of the index with the stale one read
        // before the insert. Helper closure to compare the JSON `owner` field
        // (a string or null) against the optional caller `owner`.
        let uploads_db_path = crate::pyos::path::join(&self.upload_dir, "uploads.json");
        let owner_eq = |info: &Value| -> bool {
            match info.get("owner") {
                Some(Value::String(s)) => owner == Some(s.as_str()),
                Some(Value::Null) | None => owner.is_none(),
                _ => false,
            }
        };

        let mut existing_file: Option<Value> = None;
        let mut existing_key: Option<String> = None;
        {
            let _index = self.index_lock.lock().unwrap();
            let mut existing_files = self.load_upload_index();
            // for key, info in existing_files.items(): hash+owner match ->
            //   if stored_path exists & inside upload_dir: keep; else stale.
            let mut stale_keys: Vec<String> = Vec::new();
            for (key, info) in existing_files.iter() {
                let hash_match =
                    info.get("hash").and_then(|v| v.as_str()) == Some(file_hash.as_str());
                if hash_match && owner_eq(info) {
                    let stored_path = info.get("path").and_then(|v| v.as_str());
                    match stored_path {
                        Some(p)
                            if crate::pyos::path::exists(p) && self.inside_upload_dir(p) =>
                        {
                            existing_key = Some(key.clone());
                            existing_file = Some(info.clone());
                            break;
                        }
                        _ => stale_keys.push(key.clone()),
                    }
                }
            }
            // Prune stale duplicate entries (missing/relocated files) and persist.
            if !stale_keys.is_empty() {
                let n = stale_keys.len();
                for key in &stale_keys {
                    existing_files.remove(key);
                }
                match self.atomic_write_json(&uploads_db_path, &Value::Object(existing_files)) {
                    Ok(()) => logger::info(&format!(
                        "Removed {n} stale upload index entries for missing duplicates"
                    )),
                    Err(e) => logger::warning(&format!(
                        "Failed to remove stale upload index entries: {e}"
                    )),
                }
            }
        }

        if let Some(mut existing_file) = existing_file.take() {
            let id = existing_file
                .get("id")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            logger::info(&format!(
                "Duplicate file upload detected: {original_filename} -> {id}"
            ));

            // existing_file["last_accessed"] = datetime.now().isoformat()
            if let Some(obj) = existing_file.as_object_mut() {
                obj.insert("last_accessed".to_string(), Value::String(now_isoformat()));
            }

            // Re-load under the lock; the key may have shifted under a concurrent
            // insert, so re-resolve it by hash+owner. If no entry remains, fall
            // through to the fresh-insert path.
            let mut still_duplicate = true;
            {
                let _index = self.index_lock.lock().unwrap();
                let mut current = self.load_upload_index();
                let mut live_key = existing_key.clone();
                if live_key
                    .as_ref()
                    .map(|k| !current.contains_key(k))
                    .unwrap_or(true)
                {
                    live_key = None;
                    for (k, v) in current.iter() {
                        let hash_match =
                            v.get("hash").and_then(|x| x.as_str()) == Some(file_hash.as_str());
                        if hash_match && owner_eq(v) {
                            live_key = Some(k.clone());
                            existing_file = v.clone();
                            break;
                        }
                    }
                }
                match live_key {
                    None => {
                        // raise LookupError -> except: existing_file = None
                        still_duplicate = false;
                    }
                    Some(live_key) => {
                        if let Some(obj) = existing_file.as_object_mut() {
                            obj.insert(
                                "last_accessed".to_string(),
                                Value::String(now_isoformat()),
                            );
                        }
                        current.insert(live_key, existing_file.clone());
                        if let Err(e) =
                            self.atomic_write_json(&uploads_db_path, &Value::Object(current))
                        {
                            logger::warning(&format!("Failed to update uploads database: {e}"));
                        }
                    }
                }
            }

            if still_duplicate {
                let g = |k: &str| existing_file.get(k).cloned().unwrap_or(Value::Null);
                let mut out = Map::new();
                out.insert("id".to_string(), g("id"));
                out.insert("path".to_string(), g("path"));
                out.insert("mime".to_string(), g("mime"));
                out.insert("size".to_string(), g("size"));
                out.insert("name".to_string(), g("original_name"));
                out.insert("hash".to_string(), Value::String(file_hash));
                out.insert("uploaded_at".to_string(), g("uploaded_at"));
                out.insert("owner".to_string(), g("owner"));
                out.insert("width".to_string(), g("width"));
                out.insert("height".to_string(), g("height"));
                out.insert("is_duplicate".to_string(), Value::Bool(true));
                return Ok(out);
            }
        }

        // --- Generate unique ID + save location ---
        // file_id = _build_upload_id(safe_filename)
        let file_id = build_upload_id(&safe_filename);

        let upload_dir = self.get_upload_dir();
        let file_path = crate::pyos::path::join(&upload_dir, &file_id);

        // Save the file.
        if let Err(e) = std::fs::write(&file_path, content) {
            return Err(HttpException::new(
                500,
                format!("Failed to save file: {e}"),
            ));
        }

        // --- File metadata ---
        let uploaded_at = now_isoformat();
        let mut file_metadata = Map::new();
        file_metadata.insert("id".to_string(), Value::String(file_id.clone()));
        file_metadata.insert("path".to_string(), Value::String(file_path.clone()));
        file_metadata.insert("mime".to_string(), Value::String(content_type.clone()));
        file_metadata.insert("size".to_string(), json!(file_size));
        file_metadata.insert("name".to_string(), Value::String(safe_filename.clone()));
        file_metadata.insert("hash".to_string(), Value::String(file_hash.clone()));
        file_metadata.insert(
            "original_name".to_string(),
            Value::String(original_filename.clone()),
        );
        file_metadata.insert("uploaded_at".to_string(), Value::String(uploaded_at.clone()));
        file_metadata.insert("last_accessed".to_string(), Value::String(uploaded_at));
        file_metadata.insert("client_ip".to_string(), Value::String(client_ip.to_string()));
        file_metadata.insert(
            "owner".to_string(),
            owner.map(|o| Value::String(o.to_string())).unwrap_or(Value::Null),
        );

        // Capture image dimensions. Python uses PIL + ImageOps.exif_transpose;
        // the Rust port reads the intrinsic pixel dimensions via the `image`
        // crate. HONEST SIMPLIFICATION: EXIF orientation is NOT applied, so for
        // an image tagged with a 90/270-degree rotation the width/height are not
        // swapped (they match the raw pixel grid, not the display orientation).
        if content_type.starts_with("image/") {
            match image::image_dimensions(&file_path) {
                Ok((w, h)) => {
                    file_metadata.insert("width".to_string(), json!(w));
                    file_metadata.insert("height".to_string(), json!(h));
                }
                Err(e) => logger::warning(&format!(
                    "Failed to read image dimensions for {file_id}: {e}"
                )),
            }
        }

        // --- Update uploads database (under index_lock, atomic write) ---
        {
            let _index = self.index_lock.lock().unwrap();
            // current = self._load_upload_index() if os.path.exists(...) else {}
            let mut all_files = if crate::pyos::path::exists(&uploads_db_path) {
                self.load_upload_index()
            } else {
                Map::new()
            };
            // storage_key = f"{owner}:{file_hash}" if owner else file_hash
            let storage_key = match owner {
                Some(o) => format!("{o}:{file_hash}"),
                None => file_hash.clone(),
            };
            all_files.insert(storage_key, Value::Object(file_metadata.clone()));
            if let Err(e) = self.atomic_write_json(&uploads_db_path, &Value::Object(all_files)) {
                logger::warning(&format!("Failed to update uploads database: {e}"));
            }
        }

        logger::info(&format!(
            "File uploaded successfully: {original_filename} ({file_size} bytes)"
        ));
        Ok(file_metadata)
    }
}

// ---------------------------------------------------------------------------
// UploadHandlerLike impl — wires the real handler into build_user_content /
// ChatHandler. The trait's predicates always pass the resolved mime as
// `content_type`, so they forward `Some(content_type)` into the Python
// `(filename, content_type=None)` surface.
// ---------------------------------------------------------------------------

impl UploadHandlerLike for UploadHandler {
    fn validate_upload_id(&self, upload_id: &str) -> bool {
        UploadHandler::validate_upload_id(self, upload_id)
    }
    fn inside_base_dir(&self, path: &str) -> bool {
        UploadHandler::inside_base_dir(self, path)
    }
    fn is_image_file(&self, filename: &str, content_type: &str) -> bool {
        UploadHandler::is_image_file(self, filename, Some(content_type))
    }
    fn is_audio_file(&self, filename: &str, content_type: &str) -> bool {
        UploadHandler::is_audio_file(self, filename, Some(content_type))
    }
    fn is_document_file(&self, filename: &str, content_type: &str) -> bool {
        UploadHandler::is_document_file(self, filename, Some(content_type))
    }
}

// ---------------------------------------------------------------------------
// validate_upload_id  (module-level, also exposed as a free function)
// ---------------------------------------------------------------------------

/// `is_valid_upload_id`:
/// `UPLOAD_ID_RE.fullmatch(upload_id or "") is not None`, where
/// `UPLOAD_ID_RE = re.compile(r"^[0-9a-fA-F]{32}(?:\.[A-Za-z0-9]+)?$")`.
///
/// The extension is OPTIONAL: `save_upload` builds the id as `{uuid.hex}{ext}`,
/// and a file with no extension (Dockerfile, README, ...) yields a bare 32-hex
/// id. Requiring `.ext` made those ids fail validation, so the stored file
/// could never be resolved or downloaded again.
pub fn validate_upload_id(upload_id: &str) -> bool {
    use std::sync::OnceLock;
    static RE: OnceLock<regex::Regex> = OnceLock::new();
    let re = RE.get_or_init(|| {
        // `re.fullmatch` anchors both ends; the pattern already has ^...$, and
        // the regex crate's `is_match` is unanchored, so we keep the anchors.
        regex::Regex::new(r"^[0-9a-fA-F]{32}(?:\.[A-Za-z0-9]+)?$").unwrap()
    });
    re.is_match(upload_id)
}

// ---------------------------------------------------------------------------
// Small helpers (faithful to the Python idioms used above)
// ---------------------------------------------------------------------------

/// `os.path.splitext(p)[1]` — the extension *including* the leading dot, or "".
///
/// Faithful to CPython `splitext`: a leading-dot basename has no extension, and
/// only the final dot in the basename counts.
fn splitext_ext(p: &str) -> String {
    let base = crate::pyos::path::basename(p);
    match base.rfind('.') {
        Some(idx) if base[..idx].chars().all(|c| c == '.') => String::new(),
        Some(idx) => base[idx..].to_string(),
        None => String::new(),
    }
}

/// `datetime.now().isoformat()` (LOCAL time). `datetime.now()` always carries
/// microseconds, so the result always includes a 6-digit fractional part.
fn now_isoformat() -> String {
    use chrono::Local;
    Local::now()
        .naive_local()
        .format("%Y-%m-%dT%H:%M:%S%.6f")
        .to_string()
}

/// `round(x, 2)` — banker's rounding to 2 decimals (Python 3 `round`).
fn round2(x: f64) -> f64 {
    let scaled = x * 100.0;
    let rounded = scaled.round();
    // Python's round() uses banker's rounding (round-half-to-even). Apply the
    // even adjustment only at the exact .5 boundary.
    let frac = scaled - scaled.floor();
    let rounded = if (frac - 0.5).abs() < f64::EPSILON {
        let lower = scaled.floor();
        if (lower as i64) % 2 == 0 {
            lower
        } else {
            lower + 1.0
        }
    } else {
        rounded
    };
    rounded / 100.0
}

/// Format a float the way CPython's `str(float)`/f-string does for the
/// `max_upload_size/1024/1024` MB-limit message (which is exactly `10.0`).
fn py_float_repr(x: f64) -> String {
    if x.fract() == 0.0 && x.is_finite() {
        // Python prints e.g. 10.0 (one trailing zero), not "10".
        format!("{x:.1}")
    } else {
        format!("{x}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn handler() -> UploadHandler {
        let dir = std::env::temp_dir().join(format!(
            "uh_test_{}_{}",
            std::process::id(),
            uuid::Uuid::new_v4().simple()
        ));
        UploadHandler::new(dir.to_str().unwrap(), dir.to_str().unwrap())
    }

    #[test]
    fn validate_upload_id_pattern() {
        let h = handler();
        // 32 hex + "." + alphanum ext.
        assert!(h.validate_upload_id("0123456789abcdef0123456789ABCDEF.png"));
        assert!(h.validate_upload_id("ffffffffffffffffffffffffffffffff.JPEG"));
        // The extension is OPTIONAL: a bare 32-hex id (extensionless upload such
        // as Dockerfile/README) is valid (#1346 follow-up: optional-ext regex).
        assert!(h.validate_upload_id("0123456789abcdef0123456789abcdef"));
        // Wrong length.
        assert!(!h.validate_upload_id("0123456789abcdef.png"));
        assert!(!h.validate_upload_id("0123456789abcdef")); // too-short bare id
        // Non-hex char in the id.
        assert!(!h.validate_upload_id("g123456789abcdef0123456789abcdef.png"));
        // A trailing dot with NO extension is still invalid (`.` requires alnum).
        assert!(!h.validate_upload_id("0123456789abcdef0123456789abcdef."));
        // Extension with a dot/dash (not alphanum).
        assert!(!h.validate_upload_id("0123456789abcdef0123456789abcdef.p-g"));
        // Trailing junk (fullmatch rejects).
        assert!(!h.validate_upload_id("0123456789abcdef0123456789abcdef.png\n"));
    }

    #[test]
    fn build_upload_id_sanitizes_extension() {
        // Plain extension -> "<32 hex>.png".
        let id = build_upload_id("report.png");
        assert!(validate_upload_id(&id), "id {id} must validate");
        assert!(id.ends_with(".png"));
        // No extension -> bare 32-hex id (valid under the optional-ext regex).
        let id = build_upload_id("Dockerfile");
        assert!(validate_upload_id(&id), "bare id {id} must validate");
        assert!(!id.contains('.'));
        // secure_filename keeps '-'/'_': a ".jpg-1" / ".v1_final" extension is
        // sanitized to alnum-only so the id still validates (the bug this fixes).
        let id = build_upload_id("photo.jpg-1");
        assert!(validate_upload_id(&id), "id {id} must validate");
        assert!(id.ends_with(".jpg1"));
        let id = build_upload_id("draft.v1_final");
        assert!(validate_upload_id(&id), "id {id} must validate");
        assert!(id.ends_with(".v1final"));
    }

    #[test]
    fn count_recent_uploads_ignores_batch_size() {
        // No timestamps -> 0.
        assert_eq!(count_recent_uploads(&[], 100.0, 10.0), 0);
        // Two within the 10s window, one outside (> window ago).
        let ts = [95.0, 97.0, 80.0];
        assert_eq!(count_recent_uploads(&ts, 100.0, 10.0), 2);
        // The count is of timestamps, not batch size: #1346 — a single 6-file
        // batch with no prior recent uploads counts 0, not 6.
        assert_eq!(count_recent_uploads(&[], 100.0, 10.0), 0);
    }

    #[test]
    fn secure_filename_parity() {
        // Plain name passes through.
        assert_eq!(secure_filename("report.pdf"), "report.pdf");
        // Path separators -> "_".
        assert_eq!(secure_filename("a/b\\c.txt"), "a_b_c.txt");
        // Spaces collapse to a single "_".
        assert_eq!(secure_filename("my   file.txt"), "my_file.txt");
        // Non-ASCII (é) is dropped after NFKD: "café" -> "cafe".
        assert_eq!(secure_filename("café.txt"), "cafe.txt");
        // Disallowed punctuation removed.
        assert_eq!(secure_filename("a!@#$b.txt"), "ab.txt");
        // Leading dots stripped (no dotfiles).
        assert_eq!(secure_filename("...hidden.txt"), "hidden.txt");
        // Path separators become underscores (kept as `\w`), so "///" -> "___"
        // (Python: the `_` survives the `[^\w\s\-.]` strip and is truthy).
        assert_eq!(secure_filename("///"), "___");
        // All-dots -> empty after lstrip(".") -> "unnamed".
        assert_eq!(secure_filename("..."), "unnamed");
    }

    #[test]
    fn file_type_predicates() {
        let h = handler();
        // Image by extension.
        assert!(h.is_image_file("pic.PNG", None));
        assert!(h.is_image_file("x.jpeg", None));
        assert!(!h.is_image_file("x.txt", None));
        // Image by content type.
        assert!(h.is_image_file("x.bin", Some("image/webp")));
        // Document by extension and mime.
        assert!(h.is_document_file("a.rs", None));
        assert!(h.is_document_file("a.bin", Some("application/pdf")));
        assert!(!h.is_document_file("a.bin", Some("image/png")));
        // Audio.
        assert!(h.is_audio_file("v.m4a", None));
        assert!(h.is_audio_file("v.bin", Some("audio/ogg")));
        assert!(!h.is_audio_file("v.txt", None));
    }

    #[test]
    fn is_safe_file_type_rejects_dangerous() {
        let h = handler();
        assert!(!h.is_safe_file_type("application/x-msdownload", "ok.txt"));
        assert!(!h.is_safe_file_type("text/plain", "evil.exe"));
        assert!(!h.is_safe_file_type("application/javascript", "ok.txt"));
        assert!(h.is_safe_file_type("text/plain", "fine.txt"));
        assert!(h.is_safe_file_type("application/pdf", "doc.pdf"));
    }

    #[test]
    fn detect_content_type_sniff_then_fallback() {
        let h = handler();
        // PNG magic bytes -> sniffed as image/png.
        let png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR";
        assert_eq!(h.detect_content_type(png, "whatever.bin"), "image/png");
        // No magic match, but a .txt extension -> mimetypes fallback (text/plain).
        let plain = b"just some plain text content here";
        assert_eq!(h.detect_content_type(plain, "notes.txt"), "text/plain");
        // No magic, no useful extension -> octet-stream default.
        assert_eq!(
            h.detect_content_type(plain, "noext"),
            "application/octet-stream"
        );
    }

    #[test]
    fn save_upload_and_stats_roundtrip() {
        let h = handler();
        let content = b"hello world, this is a text file";
        let res = h
            .save_upload(content, Some("note.txt"), "127.0.0.1", Some("alice"))
            .expect("save ok");
        assert_eq!(res.get("name").and_then(|v| v.as_str()), Some("note.txt"));
        assert_eq!(res.get("mime").and_then(|v| v.as_str()), Some("text/plain"));
        assert_eq!(res.get("owner").and_then(|v| v.as_str()), Some("alice"));
        assert_eq!(res.get("size").and_then(|v| v.as_u64()), Some(content.len() as u64));

        // A second identical upload for the same owner is a duplicate.
        let dup = h
            .save_upload(content, Some("note.txt"), "127.0.0.1", Some("alice"))
            .expect("dup ok");
        assert_eq!(dup.get("is_duplicate").and_then(|v| v.as_bool()), Some(true));

        // Stats reflect the single stored (deduped) file.
        let stats = h.get_upload_stats();
        assert_eq!(stats.get("total_files").and_then(|v| v.as_u64()), Some(1));
        assert_eq!(stats.get("cleanup_days").and_then(|v| v.as_i64()), Some(30));

        let _ = std::fs::remove_dir_all(h.upload_dir());
    }

    #[test]
    fn save_upload_rejects_empty_and_dangerous() {
        let h = handler();
        // Empty file -> 400.
        let err = h.save_upload(b"", Some("x.txt"), "1.1.1.1", None).unwrap_err();
        assert_eq!(err.status_code, 400);
        assert_eq!(err.detail, "File is empty");

        // .exe extension is a dangerous file type -> 400.
        let err = h
            .save_upload(b"MZ\x90\x00binary", Some("virus.exe"), "1.1.1.1", None)
            .unwrap_err();
        assert_eq!(err.status_code, 400);
        assert!(err.detail.starts_with("File type not allowed"));

        let _ = std::fs::remove_dir_all(h.upload_dir());
    }

    #[test]
    fn save_upload_rate_limit() {
        let h = handler();
        // The per-window limit was raised from 5 to 60 (#1346): a 6+ file batch
        // must NOT self-reject. 60 distinct uploads succeed (each unique content
        // avoids dedupe), the 61st within the window is rejected with 429.
        assert_eq!(h.upload_rate_limit, 60);
        for i in 0..60 {
            let body = format!("file number {i}");
            h.save_upload(body.as_bytes(), Some("f.txt"), "9.9.9.9", None)
                .expect("under limit");
        }
        let err = h
            .save_upload(b"file number 60", Some("f.txt"), "9.9.9.9", None)
            .unwrap_err();
        assert_eq!(err.status_code, 429);

        let _ = std::fs::remove_dir_all(h.upload_dir());
    }

    #[test]
    fn count_recent_uploads_method_ignores_batch() {
        let h = handler();
        // No prior uploads from this IP -> 0 regardless of would-be batch size.
        assert_eq!(h.count_recent_uploads("3.3.3.3"), 0);
        // One real upload records one recent timestamp.
        h.save_upload(b"abc", Some("a.txt"), "3.3.3.3", None)
            .expect("save");
        assert_eq!(h.count_recent_uploads("3.3.3.3"), 1);
        // A different IP is still 0 (per-IP).
        assert_eq!(h.count_recent_uploads("4.4.4.4"), 0);
        let _ = std::fs::remove_dir_all(h.upload_dir());
    }

    #[test]
    fn resolver_owner_scoping_and_containment() {
        let h = handler();
        let content = b"resolver test content";
        let meta = h
            .save_upload(content, Some("doc.txt"), "1.2.3.4", Some("alice"))
            .expect("save");
        let id = meta.get("id").and_then(|v| v.as_str()).unwrap().to_string();

        // get_upload_info returns the stored row.
        let info = h.get_upload_info(&id).expect("info present");
        assert_eq!(info.get("owner").and_then(|v| v.as_str()), Some("alice"));

        // find_upload_path locates the on-disk file, inside the upload dir.
        let path = h.find_upload_path(&id).expect("path found");
        assert!(h.inside_upload_dir(&path));

        // resolve_upload as the owner succeeds and fills the resolver fields.
        let r = h
            .resolve_upload(&id, Some("alice"), None, true)
            .expect("owner resolves");
        assert_eq!(r.get("id").and_then(|v| v.as_str()), Some(id.as_str()));
        assert_eq!(r.get("mime").and_then(|v| v.as_str()), Some("text/plain"));
        assert!(h.inside_upload_dir(r.get("path").and_then(|v| v.as_str()).unwrap()));

        // A different owner (no auth_manager => no admin) is denied.
        assert!(h.resolve_upload(&id, Some("mallory"), None, true).is_none());

        // An owned upload cannot be resolved with no owner at all.
        assert!(h.resolve_upload(&id, None, None, true).is_none());

        // Invalid id format -> None.
        assert!(h.resolve_upload("not-a-valid-id", Some("alice"), None, true).is_none());
        assert!(h.get_upload_info("not-a-valid-id").is_none());
        assert!(h.find_upload_path("not-a-valid-id").is_none());

        let _ = std::fs::remove_dir_all(h.upload_dir());
    }

    #[test]
    fn resolve_upload_unowned_without_auth() {
        let h = handler();
        // An upload saved with NO owner is resolvable with no owner (the legacy
        // un-authenticated path), but only because info["owner"] is null.
        let meta = h
            .save_upload(b"public bytes", Some("p.txt"), "1.1.1.1", None)
            .expect("save");
        let id = meta.get("id").and_then(|v| v.as_str()).unwrap().to_string();
        assert!(h.resolve_upload(&id, None, None, true).is_some());
        let _ = std::fs::remove_dir_all(h.upload_dir());
    }

    #[test]
    fn uploads_json_written_atomically_with_bak() {
        let h = handler();
        // First save creates uploads.json (no .bak yet — nothing to back up).
        h.save_upload(b"first file", Some("a.txt"), "1.1.1.1", Some("u"))
            .expect("save 1");
        let db = crate::pyos::path::join(h.upload_dir(), "uploads.json");
        let bak = format!("{db}.bak");
        assert!(crate::pyos::path::exists(&db));
        assert!(!crate::pyos::path::exists(&bak));

        // A second (distinct) save overwrites uploads.json and leaves a .bak of
        // the previous good state.
        h.save_upload(b"second file", Some("b.txt"), "1.1.1.1", Some("u"))
            .expect("save 2");
        assert!(crate::pyos::path::exists(&bak));
        // The live file parses to a JSON object with both entries.
        let raw = std::fs::read_to_string(&db).unwrap();
        let parsed: Value = serde_json::from_str(&raw).unwrap();
        assert_eq!(parsed.as_object().map(|o| o.len()), Some(2));
        let _ = std::fs::remove_dir_all(h.upload_dir());
    }

    #[test]
    fn load_index_falls_back_to_bak_on_corrupt_live() {
        let h = handler();
        h.save_upload(b"good content", Some("a.txt"), "1.1.1.1", Some("u"))
            .expect("save");
        let db = crate::pyos::path::join(h.upload_dir(), "uploads.json");
        let bak = format!("{db}.bak");
        // Seed a valid .bak by copying the good live file, then corrupt live.
        std::fs::copy(&db, &bak).unwrap();
        std::fs::write(&db, b"{ this is not valid json").unwrap();
        // The loader prefers the live file, fails to parse, then falls back to
        // the .bak sibling.
        let idx = h.load_upload_index();
        assert_eq!(idx.len(), 1);
        let _ = std::fs::remove_dir_all(h.upload_dir());
    }

    #[test]
    fn dedupe_prunes_stale_index_entry() {
        let h = handler();
        let content = b"dedupe content here";
        let meta = h
            .save_upload(content, Some("a.txt"), "1.1.1.1", Some("u"))
            .expect("save");
        let stored_path = meta.get("path").and_then(|v| v.as_str()).unwrap().to_string();
        // Delete the on-disk file so the index entry is stale, then re-upload the
        // same content+owner: the stale entry is pruned and a fresh entry made
        // (NOT reported as a duplicate, since the original bytes are gone).
        std::fs::remove_file(&stored_path).unwrap();
        let again = h
            .save_upload(content, Some("a.txt"), "1.1.1.1", Some("u"))
            .expect("re-save");
        assert!(again.get("is_duplicate").is_none());
        // Exactly one (fresh) entry remains in the index.
        let idx = h.load_upload_index();
        assert_eq!(idx.len(), 1);
        let _ = std::fs::remove_dir_all(h.upload_dir());
    }

    #[test]
    fn upload_handler_implements_trait() {
        // Compile-time + runtime check that the real handler satisfies the
        // UploadHandlerLike surface used by build_user_content / ChatHandler.
        fn assert_like<U: UploadHandlerLike>(_u: &U) {}
        let h = handler();
        assert_like(&h);
        assert!(<UploadHandler as UploadHandlerLike>::is_image_file(
            &h,
            "a.png",
            "image/png"
        ));
    }

    #[test]
    fn round2_parity() {
        assert_eq!(round2(0.0), 0.0);
        assert_eq!(round2(1.005 * 1.0), 1.0); // floating noise -> 1.0 region
        assert_eq!(round2(10.0 / (1024.0 * 1024.0) * (1024.0 * 1024.0)), 10.0);
    }
}
