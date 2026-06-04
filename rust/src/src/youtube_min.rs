// src/youtube_min.rs  <- src/youtube_handler.py
//! Port of `src/youtube_handler.py`: the YouTube URL predicate, the video-id
//! extractor, the two context-formatting functions, the
//! `YOUTUBE_INSTRUCTION_PROMPT` constant, and — now REAL — the two network
//! halves `extract_transcript_async` and `fetch_youtube_comments`. The
//! formatters are consumed by `chat_handler` to assemble LLM context from the
//! fetched transcript / comment data.
//!
//! NETWORK FETCHERS (both `async`, both yt-dlp-backed):
//!
//! * [`fetch_youtube_comments`] is a faithful 1:1 port of the Python: it shells
//!   out to the `yt-dlp` binary with `--skip-download --write-comments
//!   --dump-json` (exact flag order preserved), parses the single JSON object on
//!   stdout, filters/maps the comments, and stable-sorts them by likes
//!   descending. The whole subprocess is bounded by the Python `timeout`.
//!
//! * [`extract_transcript_async`] reproduces the SAME `transcript_data` shape
//!   the Python returns, but via a different transport (documented drift below).
//!   The Python uses the `youtube-transcript-api` package (an in-process HTTP
//!   client). Here we delegate to the `yt-dlp` subprocess writing `json3`
//!   subtitles (`--skip-download --write-auto-subs --write-subs --sub-format
//!   json3 --sub-langs en.*`) into a per-attempt temp dir, then parse the
//!   `json3` events into the same per-segment `{text,start,duration,timestamp}`
//!   shape (`tStartMs`/`dDurationMs` are milliseconds -> divide by 1000.0 for
//!   the float seconds that match the api's `snippet.start`/`snippet.duration`).
//!
//! FAITHFUL DRIFT (transcript only):
//! * MECHANISM: `youtube-transcript-api` (in-process HTTP) -> `yt-dlp` subprocess
//!   writing `json3` subtitles. Same `transcript_data` shape, different
//!   transport. yt-dlp is the actively-maintained component that already solves
//!   YouTube's consent walls / signature deciphering / PO-token challenges, so
//!   delegating to it is the robust faithful choice (raw-HTTP timedtext scraping
//!   — which `youtube-transcript-api` itself does and which breaks regularly —
//!   was deliberately NOT chosen).
//! * AUTO vs MANUAL: the Python hardcodes `is_generated=False` (labels every
//!   track "Manual"). yt-dlp may surface an auto-generated track when no manual
//!   one exists; we detect & report `is_generated` from yt-dlp's "automatic"
//!   stdout/stderr marker (defaulting to `false` when undetectable, matching the
//!   Python default). A faithful superset: we can correctly label auto tracks
//!   the Python silently mislabels as Manual.
//! * LANGUAGE FALLBACK: the Python labels whatever the api returns as `"en"`; we
//!   request `--sub-langs en.*` (en + regional + auto-en) and likewise hardcode
//!   `language="en"` in the output. A non-English-only video for which the
//!   Python would fetch the default track (and still call it "en") yields no
//!   en-track here -> the honest failure shape (no fake success).
//! * TIMEOUT: the Python transcript path is unbounded (in-process); a network
//!   subprocess MUST be bounded, so each attempt is capped at 30s (matching the
//!   comments timeout) with a kill + retry on elapse. No Python analogue — this
//!   is a necessary addition for the subprocess transport.

use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::time::Duration;
use url::Url;

use crate::pylog as logger;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// Structured-breakdown instruction injected when the user shares a YouTube link.
pub const YOUTUBE_INSTRUCTION_PROMPT: &str = "When the user shares a YouTube video, respond with a structured breakdown:

1. **Summary** — Concise overview of the video's content and main thesis (2-4 sentences)
2. **Key Points** — Bullet list of the most important topics, arguments, or moments
3. **Notable Timestamps** — If timestamps are available from the transcript, highlight 3-5 interesting moments with their approximate timestamps (e.g. \"03:45 — discusses X\")
4. **Audience Reception** — If comments are available, summarize what viewers think: general sentiment, top reactions, any debate or controversy

Keep it conversational and concise. Do NOT web search for this video — use only the transcript and comments provided.";

// ---------------------------------------------------------------------------
// Pure predicates / parsers
// ---------------------------------------------------------------------------

/// `"youtube.com" in url or "youtu.be" in url`.
pub fn is_youtube_url(url: &str) -> bool {
    url.contains("youtube.com") || url.contains("youtu.be")
}

/// Extract YouTube video ID from various URL formats.
///
/// Mirrors `youtube_handler.extract_youtube_id`:
/// - `(www|m|)youtube.com/watch?v=ID` -> first `v` query value
/// - `(www|m|)youtube.com/embed/ID` -> last path segment
/// - `youtu.be/ID` -> path without the leading `/`
///
/// PARITY NOTE: Python uses `urllib.parse.urlparse`, which is more lenient than
/// the `url` crate (it does not require a scheme). In practice this function is
/// only ever fed URLs produced by `chat_helpers::extract_urls`, which match
/// `https?://…`, so they always parse. A string the `url` crate rejects maps to
/// the Python `hostname=None` path, i.e. `None`.
pub fn extract_youtube_id(url: &str) -> Option<String> {
    let parsed = match Url::parse(url) {
        Ok(u) => u,
        Err(_) => return None,
    };
    // `urlparse(...).hostname` lowercases the host (the `url` crate already
    // returns the host lowercased for special schemes like http/https).
    let hostname = parsed.host_str().unwrap_or("");
    let path = parsed.path();

    if hostname == "www.youtube.com" || hostname == "youtube.com" || hostname == "m.youtube.com" {
        if path == "/watch" {
            // params = parse_qs(query); if "v" in params: return params["v"][0]
            // `query_pairs` preserves left-to-right order, so the first `v`
            // matches `params["v"][0]`. Python's `parse_qs` defaults to
            // `keep_blank_values=False`, so a blank value (`?v=` or `?v`) does
            // not register a `v` key at all — skip empty values to match.
            if let Some((_, v)) = parsed.query_pairs().find(|(k, v)| k == "v" && !v.is_empty()) {
                return Some(v.into_owned());
            }
        } else if path.starts_with("/embed/") {
            // parsed.path.split("/")[-1]
            return Some(path.rsplit('/').next().unwrap_or("").to_string());
        }
    } else if hostname == "youtu.be" {
        // parsed.path[1:]
        return Some(path.get(1..).unwrap_or("").to_string());
    }
    None
}

// ---------------------------------------------------------------------------
// Context formatting (pure)
// ---------------------------------------------------------------------------

/// `dict.get(key)` truthiness for the `transcript_data.get("success")` guards:
/// missing / null / `false` are falsy.
fn truthy(v: &Value, key: &str) -> bool {
    match v.get(key) {
        Some(Value::Bool(b)) => *b,
        Some(Value::Null) | None => false,
        Some(other) => !is_falsy(other),
    }
}

fn is_falsy(v: &Value) -> bool {
    match v {
        Value::Null => true,
        Value::Bool(b) => !b,
        Value::Number(n) => n.as_f64().map(|f| f == 0.0).unwrap_or(false),
        Value::String(s) => s.is_empty(),
        Value::Array(a) => a.is_empty(),
        Value::Object(o) => o.is_empty(),
    }
}

fn get_str<'a>(v: &'a Value, key: &str, default: &'a str) -> String {
    match v.get(key) {
        Some(Value::String(s)) => s.clone(),
        _ => default.to_string(),
    }
}

/// `s[:n]` over Unicode scalar values (Python slices strings by code point).
fn py_slice(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// `s.index(sub)` — byte offset of the first occurrence (the slice `s[:idx]`
/// below cuts on a UTF-8 boundary because the needle is pure ASCII).
fn str_index(s: &str, sub: &str) -> Option<usize> {
    s.find(sub)
}

/// Format transcript data for inclusion in LLM context.
///
/// `transcript_data` is the dict returned by `extract_transcript_async`
/// (modelled as a `serde_json::Value` object). When `success` is falsy the
/// function emits the "Transcript unavailable" header instead of a transcript.
pub fn format_transcript_for_context(
    transcript_data: &Value,
    url: &str,
    title: &str,
    channel: &str,
) -> String {
    if !truthy(transcript_data, "success") {
        let mut header = String::new();
        if !title.is_empty() {
            header = format!(" \"{title}\"");
            if !channel.is_empty() {
                header += &format!(" by {channel}");
            }
        }
        let err = get_str(transcript_data, "error", "Unknown error");
        return format!(
            "\n[YouTube Video{header}: Transcript unavailable ({err}). Use the comments below if available, do NOT web search for this video.]"
        );
    }

    let transcript = get_str(transcript_data, "transcript", "");
    let video_id = get_str(transcript_data, "video_id", "");
    let language = get_str(transcript_data, "language", "unknown");
    // `transcript_data.get("is_generated", False)` — only an explicit `true`
    // selects "Auto-generated".
    let is_generated = matches!(transcript_data.get("is_generated"), Some(Value::Bool(true)));
    let segments: &[Value] = match transcript_data.get("segments") {
        Some(Value::Array(a)) => a.as_slice(),
        _ => &[],
    };

    let mut ctx = String::from("\n[YOUTUBE VIDEO TRANSCRIPT]\n");
    if !title.is_empty() {
        ctx += &format!("Title: {title}\n");
    }
    if !channel.is_empty() {
        ctx += &format!("Channel: {channel}\n");
    }
    ctx += &format!("Video ID: {video_id}\n");
    ctx += &format!("Language: {language}\n");
    ctx += &format!(
        "Source: {}\n",
        if is_generated { "Auto-generated" } else { "Manual" }
    );
    ctx += &format!("URL: {url}\n\n");

    if !segments.is_empty() {
        ctx += "Timestamped Transcript:\n";
        for seg in segments {
            let ts = get_str(seg, "timestamp", "");
            let text = get_str(seg, "text", "");
            ctx += &format!("[{ts}] {text}\n");
        }
        // `if len(ctx) > 12000` — Python `len` counts code points.
        if ctx.chars().count() > 12000 {
            // ctx = ctx[:ctx.index("Timestamped Transcript:\n")]
            if let Some(idx) = str_index(&ctx, "Timestamped Transcript:\n") {
                ctx.truncate(idx);
            }
            ctx += "Transcript:\n";
            ctx += &transcript;
        }
    } else {
        ctx += "Transcript:\n";
        ctx += &transcript;
    }
    ctx += "\n[END TRANSCRIPT]\n";
    ctx
}

/// Format YouTube comments for inclusion in LLM context.
///
/// Returns an empty string when the fetch failed or there are no comments
/// (matching the Python early-return).
pub fn format_comments_for_context(comments_data: &Value, url: &str) -> String {
    // `if not comments_data.get("success") or not comments_data.get("comments")`
    if !truthy(comments_data, "success") || !truthy(comments_data, "comments") {
        return String::new();
    }

    let comments: &[Value] = match comments_data.get("comments") {
        Some(Value::Array(a)) => a.as_slice(),
        _ => return String::new(),
    };

    let mut ctx = format!(
        "\n[YOUTUBE VIDEO COMMENTS — Top {} by popularity]\n",
        comments.len()
    );
    ctx += &format!("URL: {url}\n\n");

    // `for i, c in enumerate(comments, 1)` — 1-based index.
    for (idx, c) in comments.iter().enumerate() {
        let i = idx + 1;
        // Parity guard: non-object entries in the comments array would cause
        // `c.get("author")` etc. to silently return defaults, emitting a
        // degenerate "@: \n\n" line. Skip them instead (mirrors defensive
        // coding intent; in Python `c['author']` on a non-dict would raise).
        if !c.is_object() {
            continue;
        }
        // `likes = c.get("likes", 0)` then `f" [{likes} likes]" if likes else ""`
        let likes = c.get("likes").and_then(|v| v.as_i64()).unwrap_or(0);
        let likes_str = if likes != 0 {
            format!(" [{likes} likes]")
        } else {
            String::new()
        };
        let author = get_str(c, "author", "");
        let text = get_str(c, "text", "");
        ctx += &format!("{i}. @{author}{likes_str}: {text}\n\n");
    }

    // `if len(ctx) > 4000: ctx = ctx[:4000] + "\n[Comments truncated]\n"`
    if ctx.chars().count() > 4000 {
        ctx = py_slice(&ctx, 4000) + "\n[Comments truncated]\n";
    }

    ctx += "[END COMMENTS]\n";
    ctx
}

// ---------------------------------------------------------------------------
// yt-dlp discovery / temp-dir helpers
// ---------------------------------------------------------------------------

/// `shutil.which` for an executable name: scan each `PATH` entry for a file of
/// that name, returning the first hit (executable bit checked on Unix).
///
/// Verbatim copy of `builtin_mcp::shutil_which` (kept private here rather than
/// widening `builtin_mcp`'s API surface; the tree already duplicates tiny pure
/// helpers like `py_slice` across modules).
fn shutil_which(name: &str) -> Option<String> {
    let path_var = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path_var) {
        let candidate = dir.join(name);
        let meta = match std::fs::metadata(&candidate) {
            Ok(m) => m,
            Err(_) => continue,
        };
        if !meta.is_file() {
            continue;
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if meta.permissions().mode() & 0o111 == 0 {
                continue;
            }
        }
        return Some(candidate.to_string_lossy().into_owned());
    }
    None
}

/// Find the `yt-dlp` binary: venv bin first, then system `PATH`, else default
/// `"yt-dlp"`.
///
/// Mirrors `youtube_handler._find_ytdlp`: Python's `Path(sys.executable).parent
/// / "yt-dlp"` becomes `current_exe().parent() / "yt-dlp"` (the same
/// `sys.executable` analogue `builtin_mcp` uses); on a miss fall through to
/// `shutil_which`, then the bare name.
fn find_ytdlp() -> String {
    if let Some(exe_parent) = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(PathBuf::from))
    {
        let venv_bin = Path::new(&exe_parent).join("yt-dlp");
        if venv_bin.exists() {
            return venv_bin.to_string_lossy().into_owned();
        }
    }
    shutil_which("yt-dlp").unwrap_or_else(|| "yt-dlp".to_string())
}

/// A fresh per-attempt scratch dir for yt-dlp subtitle output:
/// `<temp>/odysseus_yt_<pid>_<uuid>` (honors `TMPDIR` via `std::env::temp_dir`).
///
/// Transient yt-dlp scratch belongs in the system temp dir (the runtime pattern
/// `document_processor` uses), NOT `ODYSSEUS_DATA_DIR` (the durable app data
/// dir). `tempfile` is a dev-only dependency, so this uses std + a manual
/// `remove_dir_all` cleanup at every return path.
fn yt_temp_dir() -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "odysseus_yt_{}_{}",
        std::process::id(),
        uuid::Uuid::new_v4()
    ));
    let _ = std::fs::create_dir_all(&dir);
    dir
}

// ---------------------------------------------------------------------------
// Network fetchers — yt-dlp-backed
// ---------------------------------------------------------------------------

/// Async YouTube transcript extraction with retries.
///
/// Returns the same `transcript_data` shape as the Python
/// `extract_transcript_async`, fetched via the `yt-dlp` subprocess writing
/// `json3` subtitles (see the module doc for the mechanism/auto-vs-manual/
/// language/timeout drift vs the Python `youtube-transcript-api`).
///
/// Success: `{success:true, transcript, video_id, language:"en", is_generated,
/// segments:[{text,start,duration,timestamp}]}`. Failure (after `max_retries`,
/// or yt-dlp absent): `{success:false, error, transcript:null}` — so
/// `format_transcript_for_context` degrades to the "Transcript unavailable"
/// branch instead of fabricating a transcript.
///
/// `_url` is accepted for signature parity with the Python (`url`), but unused:
/// the watch URL is rebuilt from `video_id` exactly as the Python subprocess
/// path would.
pub async fn extract_transcript_async(_url: &str, video_id: &str, max_retries: i64) -> Value {
    let ytdlp = find_ytdlp();
    let watch_url = format!("https://www.youtube.com/watch?v={video_id}");

    for attempt in 0..max_retries {
        // Fresh scratch dir per attempt (cleaned up on every exit path below).
        let tmp = yt_temp_dir();
        let out_template = tmp.join("%(id)s.%(ext)s");

        let mut cmd = tokio::process::Command::new(&ytdlp);
        cmd.arg("--skip-download")
            .arg("--write-auto-subs")
            .arg("--write-subs")
            .arg("--sub-format")
            .arg("json3")
            .arg("--sub-langs")
            .arg("en.*")
            .arg("-o")
            .arg(&out_template)
            .arg("--no-warnings")
            .arg(&watch_url)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true);

        let child = match cmd.spawn() {
            Ok(c) => c,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                // yt-dlp absent: the honest "not available" early-out, mirroring
                // the Python `YOUTUBE_AVAILABLE` guard string.
                let _ = std::fs::remove_dir_all(&tmp);
                return json!({
                    "success": false,
                    "error": "YouTube transcript API not available",
                    "transcript": Value::Null,
                });
            }
            Err(e) => {
                logger::warning(&format!("Transcript attempt {} failed: {e}", attempt + 1));
                let _ = std::fs::remove_dir_all(&tmp);
                if attempt < max_retries - 1 {
                    tokio::time::sleep(Duration::from_secs((attempt + 1) as u64)).await;
                }
                continue;
            }
        };

        // Bound the subprocess (no Python analogue — see module-doc TIMEOUT drift).
        let output = match tokio::time::timeout(Duration::from_secs(30), child.wait_with_output())
            .await
        {
            Ok(Ok(o)) => o,
            Ok(Err(e)) => {
                logger::warning(&format!("Transcript attempt {} failed: {e}", attempt + 1));
                let _ = std::fs::remove_dir_all(&tmp);
                if attempt < max_retries - 1 {
                    tokio::time::sleep(Duration::from_secs((attempt + 1) as u64)).await;
                }
                continue;
            }
            Err(_elapsed) => {
                // Timed out: `wait_with_output` consumed `child`, so kill_on_drop
                // already reaped it. Treat as a failed attempt.
                logger::warning(&format!(
                    "Transcript attempt {} timed out for {video_id}",
                    attempt + 1
                ));
                let _ = std::fs::remove_dir_all(&tmp);
                if attempt < max_retries - 1 {
                    tokio::time::sleep(Duration::from_secs((attempt + 1) as u64)).await;
                }
                continue;
            }
        };

        // Detect auto-vs-manual from yt-dlp's log markers. Python hardcodes
        // false, so default false when undetectable (faithful default).
        let logs = format!(
            "{}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        let logs_lower = logs.to_lowercase();
        let is_generated = logs_lower.contains("automatic");

        // Find the produced `<id>.<lang>.json3` (prefer first by sorted name).
        let json3_path = find_json3_file(&tmp);
        let json3_path = match json3_path {
            Some(p) => p,
            None => {
                // No captions this attempt -> failed attempt.
                logger::warning(&format!(
                    "Transcript attempt {} produced no captions for {video_id}",
                    attempt + 1
                ));
                let _ = std::fs::remove_dir_all(&tmp);
                if attempt < max_retries - 1 {
                    tokio::time::sleep(Duration::from_secs((attempt + 1) as u64)).await;
                }
                continue;
            }
        };

        let raw = match std::fs::read_to_string(&json3_path) {
            Ok(r) => r,
            Err(e) => {
                logger::warning(&format!("Transcript attempt {} failed: {e}", attempt + 1));
                let _ = std::fs::remove_dir_all(&tmp);
                if attempt < max_retries - 1 {
                    tokio::time::sleep(Duration::from_secs((attempt + 1) as u64)).await;
                }
                continue;
            }
        };

        let data: Value = match serde_json::from_str(&raw) {
            Ok(d) => d,
            Err(e) => {
                logger::warning(&format!("Transcript attempt {} failed: {e}", attempt + 1));
                let _ = std::fs::remove_dir_all(&tmp);
                if attempt < max_retries - 1 {
                    tokio::time::sleep(Duration::from_secs((attempt + 1) as u64)).await;
                }
                continue;
            }
        };

        // Parse succeeded. Build segments from json3 `events`. An empty
        // transcript (zero usable events) is STILL a success in Python spirit
        // (an empty `youtube-transcript-api` list -> success=true, transcript="").
        let mut formatted: Vec<Value> = Vec::new();
        if let Some(events) = data.get("events").and_then(|e| e.as_array()) {
            for event in events {
                // text = concat of segs[].utf8, then strip; skip empty.
                let mut text = String::new();
                if let Some(segs) = event.get("segs").and_then(|s| s.as_array()) {
                    for seg in segs {
                        if let Some(u) = seg.get("utf8").and_then(|u| u.as_str()) {
                            text.push_str(u);
                        }
                    }
                }
                let text = text.trim();
                if text.is_empty() {
                    continue;
                }
                let start = event
                    .get("tStartMs")
                    .and_then(|v| v.as_f64())
                    .unwrap_or(0.0)
                    / 1000.0;
                let duration = event
                    .get("dDurationMs")
                    .and_then(|v| v.as_f64())
                    .unwrap_or(0.0)
                    / 1000.0;
                // f"{int(start//60):02d}:{int(start%60):02d}" (start>=0 -> trunc==floor).
                let timestamp = format!("{:02}:{:02}", (start / 60.0) as i64, (start % 60.0) as i64);
                formatted.push(json!({
                    "text": text,
                    "start": start,
                    "duration": duration,
                    "timestamp": timestamp,
                }));
            }
        }

        // full_text = " ".join(segment texts); 8000-codepoint cap.
        let mut full_text = formatted
            .iter()
            .map(|e| e.get("text").and_then(|t| t.as_str()).unwrap_or(""))
            .collect::<Vec<_>>()
            .join(" ");
        let max_len = 8000;
        if full_text.chars().count() > max_len {
            full_text = py_slice(&full_text, max_len) + "... [transcript truncated]";
        }

        let _ = std::fs::remove_dir_all(&tmp);
        return json!({
            "success": true,
            "transcript": full_text,
            "video_id": video_id,
            "language": "en",
            "is_generated": is_generated,
            "segments": formatted,
        });
    }

    // After max_retries: the Python failure shape.
    json!({
        "success": false,
        "error": format!("Failed after {max_retries} attempts"),
        "transcript": Value::Null,
    })
}

/// Scan `dir` for a produced `*.json3` subtitle file, returning the first by
/// sorted filename. yt-dlp writes `<id>.<lang>.json3` (e.g. `<id>.en.json3`).
fn find_json3_file(dir: &Path) -> Option<PathBuf> {
    let entries = std::fs::read_dir(dir).ok()?;
    let mut hits: Vec<PathBuf> = entries
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| {
            p.extension()
                .and_then(|e| e.to_str())
                .map(|e| e.eq_ignore_ascii_case("json3"))
                .unwrap_or(false)
        })
        .collect();
    hits.sort();
    hits.into_iter().next()
}

/// Fetch top comments for a YouTube video using yt-dlp.
///
/// Faithful 1:1 port of the Python `fetch_youtube_comments`: the `yt-dlp`
/// subprocess with `--skip-download --write-comments --dump-json` (exact flag
/// order), bounded by `timeout`, then JSON-parse + filter/map + stable
/// likes-descending sort.
///
/// Success: `{success:true, comments:[{author,text,likes}], count, title,
/// channel}`. Failures: `{success:false, error, comments:[]}` with the same
/// error strings as the Python branches (`"yt-dlp not installed"`,
/// `"Comment fetch timed out"`, `"yt-dlp failed: <stderr[:200]>"`, or the raw
/// exception string) — so `format_comments_for_context` yields an empty string.
pub async fn fetch_youtube_comments(video_id: &str, max_comments: i64, timeout: i64) -> Value {
    let watch_url = format!("https://www.youtube.com/watch?v={video_id}");
    let extractor_args = format!("youtube:max_comments={max_comments},all,100,0");

    let mut cmd = tokio::process::Command::new(find_ytdlp());
    cmd.arg("--skip-download")
        .arg("--write-comments")
        .arg("--extractor-args")
        .arg(&extractor_args)
        .arg("--dump-json")
        .arg("--js-runtimes")
        .arg("node")
        .arg("--remote-components")
        .arg("ejs:github")
        .arg(&watch_url)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true);

    let child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            // Python `except FileNotFoundError`.
            logger::warning("yt-dlp not installed — cannot fetch comments");
            return json!({
                "success": false,
                "error": "yt-dlp not installed",
                "comments": [],
            });
        }
        Err(e) => {
            // Python broad `except Exception`.
            logger::warning(&format!("Failed to fetch comments for {video_id}: {e}"));
            return json!({
                "success": false,
                "error": e.to_string(),
                "comments": [],
            });
        }
    };

    // `wait_with_output` drains both pipes concurrently (the faithful analogue of
    // Python's `communicate()`), avoiding a pipe-buffer deadlock on large JSON.
    let output = match tokio::time::timeout(
        Duration::from_secs(timeout as u64),
        child.wait_with_output(),
    )
    .await
    {
        Ok(Ok(o)) => o,
        Ok(Err(e)) => {
            // Python broad `except Exception`.
            logger::warning(&format!("Failed to fetch comments for {video_id}: {e}"));
            return json!({
                "success": false,
                "error": e.to_string(),
                "comments": [],
            });
        }
        Err(_elapsed) => {
            // Python `except asyncio.TimeoutError` (child reaped via kill_on_drop).
            logger::warning(&format!("Comment fetch timed out for {video_id}"));
            return json!({
                "success": false,
                "error": "Comment fetch timed out",
                "comments": [],
            });
        }
    };

    // `if proc.returncode != 0`
    if output.status.code() != Some(0) {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return json!({
            "success": false,
            "error": format!("yt-dlp failed: {}", py_slice(&stderr, 200)),
            "comments": [],
        });
    }

    // `data = json.loads(stdout.decode())` — parse error lands in the broad except.
    let stdout = String::from_utf8_lossy(&output.stdout);
    let data: Value = match serde_json::from_str(&stdout) {
        Ok(d) => d,
        Err(e) => {
            logger::warning(&format!("Failed to fetch comments for {video_id}: {e}"));
            return json!({
                "success": false,
                "error": e.to_string(),
                "comments": [],
            });
        }
    };

    // title = data.get("title", "")
    let title = data.get("title").and_then(|v| v.as_str()).unwrap_or("");
    // channel = data.get("channel","") or data.get("uploader","") (truthy-or).
    let channel = {
        let c = data.get("channel").and_then(|v| v.as_str()).unwrap_or("");
        if c.is_empty() {
            data.get("uploader").and_then(|v| v.as_str()).unwrap_or("")
        } else {
            c
        }
    };

    let raw_comments = data
        .get("comments")
        .and_then(|v| v.as_array())
        .map(|a| a.as_slice())
        .unwrap_or(&[]);

    let mut comments: Vec<Value> = Vec::new();
    for c in raw_comments.iter().take(max_comments as usize) {
        // text = (c.get("text") or "").strip(); skip empty.
        let text = c.get("text").and_then(|v| v.as_str()).unwrap_or("").trim();
        if text.is_empty() {
            continue;
        }
        let author = c.get("author").and_then(|v| v.as_str()).unwrap_or("Unknown");
        // `c.get("like_count", 0)` — the default 0 fires ONLY when the KEY is
        // ABSENT. A present-but-null `like_count` yields Python `None` (stored
        // verbatim as JSON null), NOT 0 — matching `dict.get`'s missing-key
        // semantics. The raw value flows into the sort key below, so a null (or
        // any non-number) participates in Python's `<` comparison there.
        let likes = match c.get("like_count") {
            Some(v) => v.clone(),
            None => json!(0),
        };
        comments.push(json!({
            "author": author,
            "text": text,
            "likes": likes,
        }));
    }

    // `comments.sort(key=lambda x: x.get("likes", 0), reverse=True)`. The
    // "likes" key always exists on our dicts, so the `0` default never fires;
    // the key is the raw stored value. Python's list.sort compares keys with
    // `<`, which raises `TypeError` for incomparable types (e.g. a null/None
    // like_count mixed with ints) — that propagates to the broad `except
    // Exception as e` and returns `{success:false, error:str(e), comments:[]}`.
    // `py_sort_by_likes_desc` reproduces CPython's binary-insertion comparison
    // order (lists are <= max_comments, well under Timsort's galloping bound)
    // so the surfaced `str(e)` matches the FIRST failing comparison Python hits.
    if let Err(e) = py_sort_by_likes_desc(&mut comments) {
        logger::warning(&format!("Failed to fetch comments for {video_id}: {e}"));
        return json!({
            "success": false,
            "error": e,
            "comments": [],
        });
    }

    let count = comments.len();
    json!({
        "success": true,
        "comments": comments,
        "count": count,
        "title": title,
        "channel": channel,
    })
}

/// Python `<` between two `like_count` sort keys. Returns `Ok(Ordering)` when
/// both are numbers (Python's int/float/bool are mutually comparable), or
/// `Err(str(TypeError))` with the exact CPython message
/// (`'<' not supported between instances of '<lt>' and '<rt>'`) otherwise.
/// `left`/`right` are the operands of `left < right`, so the type names appear
/// in CPython's operand order.
fn py_likes_lt(left: &Value, right: &Value) -> Result<std::cmp::Ordering, String> {
    match (left.as_f64(), right.as_f64()) {
        (Some(a), Some(b)) => Ok(a.partial_cmp(&b).unwrap_or(std::cmp::Ordering::Equal)),
        _ => Err(format!(
            "'<' not supported between instances of '{}' and '{}'",
            py_value_type_name(left),
            py_value_type_name(right),
        )),
    }
}

/// CPython type name for a JSON value as it would appear in a `TypeError`
/// message (`int`/`float`/`bool`/`str`/`NoneType`/`list`/`dict`). Mirrors the
/// types yt-dlp can place in `like_count`.
fn py_value_type_name(v: &Value) -> &'static str {
    match v {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(n) => {
            if n.is_f64() && !n.is_i64() && !n.is_u64() {
                "float"
            } else {
                "int"
            }
        }
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// Sort `comments` in place by their `likes` key, descending, reproducing
/// `list.sort(key=..., reverse=True)`. CPython implements `reverse=True` by
/// reversing the list, running an ascending stable sort, then reversing again;
/// for the small lists here that ascending sort is a binary insertion sort.
/// Replicating that comparison ORDER makes the first incomparable pair (if any)
/// — and therefore the resulting `TypeError` string — match Python exactly.
fn py_sort_by_likes_desc(comments: &mut [Value]) -> Result<(), String> {
    let likes_of = |v: &Value| v.get("likes").cloned().unwrap_or_else(|| json!(0));

    // reverse=True: reverse, sort ascending (binary insertion), reverse back.
    comments.reverse();
    for i in 1..comments.len() {
        let key = comments[i].clone();
        let key_val = likes_of(&key);
        // Binary search for the insertion point in comments[..i] using `<`
        // (CPython's binarysort compares `key < comments[mid]`).
        let mut lo = 0usize;
        let mut hi = i;
        while lo < hi {
            let mid = (lo + hi) / 2;
            // Ordering::Less means key < comments[mid] is true.
            if py_likes_lt(&key_val, &likes_of(&comments[mid]))? == std::cmp::Ordering::Less {
                hi = mid;
            } else {
                lo = mid + 1;
            }
        }
        // Shift [lo, i) right by one and drop key at lo (stable).
        for j in (lo..i).rev() {
            comments[j + 1] = comments[j].clone();
        }
        comments[lo] = key;
    }
    comments.reverse();
    Ok(())
}

#[cfg(test)]
mod tests {
    // Parity checks against the live Python (`src.youtube_handler`).
    use super::*;
    use serde_json::json;

    #[test]
    fn predicate() {
        assert!(is_youtube_url("https://www.youtube.com/watch?v=x"));
        assert!(is_youtube_url("https://youtu.be/x"));
        assert!(!is_youtube_url("https://example.com"));
    }

    #[test]
    fn id_from_watch() {
        assert_eq!(
            extract_youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            Some("dQw4w9WgXcQ".to_string())
        );
        // m.youtube.com also matches.
        assert_eq!(
            extract_youtube_id("https://m.youtube.com/watch?v=abc123&t=10"),
            Some("abc123".to_string())
        );
    }

    #[test]
    fn id_from_embed() {
        assert_eq!(
            extract_youtube_id("https://youtube.com/embed/VID42"),
            Some("VID42".to_string())
        );
    }

    #[test]
    fn id_from_short() {
        assert_eq!(
            extract_youtube_id("https://youtu.be/short99"),
            Some("short99".to_string())
        );
    }

    #[test]
    fn id_none_for_non_video() {
        assert_eq!(extract_youtube_id("https://www.youtube.com/feed/subscriptions"), None);
        assert_eq!(extract_youtube_id("https://example.com/watch?v=x"), None);
    }

    #[test]
    fn transcript_unavailable_header() {
        let data = json!({"success": false, "error": "boom"});
        let out = format_transcript_for_context(&data, "https://yt/x", "My Title", "Chan");
        assert!(out.contains("\"My Title\" by Chan"));
        assert!(out.contains("Transcript unavailable (boom)"));
    }

    #[test]
    fn transcript_with_segments() {
        let data = json!({
            "success": true,
            "transcript": "hello world",
            "video_id": "vid1",
            "language": "en",
            "is_generated": true,
            "segments": [{"timestamp": "00:05", "text": "hello"}],
        });
        let out = format_transcript_for_context(&data, "https://yt/x", "", "");
        assert!(out.starts_with("\n[YOUTUBE VIDEO TRANSCRIPT]\n"));
        assert!(out.contains("Source: Auto-generated\n"));
        assert!(out.contains("[00:05] hello\n"));
        assert!(out.ends_with("\n[END TRANSCRIPT]\n"));
    }

    #[test]
    fn comments_empty_when_no_success() {
        let data = json!({"success": false, "comments": []});
        assert_eq!(format_comments_for_context(&data, "https://yt/x"), "");
    }

    #[test]
    fn comments_formatted() {
        let data = json!({
            "success": true,
            "comments": [
                {"author": "alice", "text": "great", "likes": 12},
                {"author": "bob", "text": "ok", "likes": 0},
            ],
        });
        let out = format_comments_for_context(&data, "https://yt/x");
        assert!(out.contains("Top 2 by popularity"));
        assert!(out.contains("1. @alice [12 likes]: great\n\n"));
        // likes == 0 omits the bracket suffix.
        assert!(out.contains("2. @bob: ok\n\n"));
        assert!(out.ends_with("[END COMMENTS]\n"));
    }

    // Parity for `comments.sort(key=lambda x: x.get("likes",0), reverse=True)`,
    // including the present-but-null `like_count` -> Python `None` -> TypeError
    // path. Expected values cross-checked against CPython `list.sort`.
    #[test]
    fn sort_by_likes_matches_python() {
        let mk = |id: &str, likes: Value| json!({"id": id, "likes": likes});

        let ok = |comments: Vec<Value>, expect_ids: &[&str]| {
            let mut c = comments;
            py_sort_by_likes_desc(&mut c).expect("should sort");
            let ids: Vec<String> = c
                .iter()
                .map(|v| v["id"].as_str().unwrap().to_string())
                .collect();
            assert_eq!(ids, expect_ids);
        };
        let err = |comments: Vec<Value>, msg: &str| {
            let mut c = comments;
            assert_eq!(py_sort_by_likes_desc(&mut c).unwrap_err(), msg);
        };

        // stable descending, ties keep original order
        ok(
            vec![mk("a", json!(5)), mk("b", json!(0)), mk("c", json!(5))],
            &["a", "c", "b"],
        );
        ok(
            vec![
                mk("a", json!(3)),
                mk("b", json!(10)),
                mk("c", json!(1)),
                mk("d", json!(10)),
            ],
            &["b", "d", "a", "c"],
        );
        // single element (or single null) -> no comparison, no error
        ok(vec![mk("a", json!(5))], &["a"]);
        ok(vec![mk("a", Value::Null)], &["a"]);
        // present-but-null `like_count` mixed with ints raises Python TypeError;
        // the operand order in the message matches CPython's comparison order.
        err(
            vec![mk("a", json!(5)), mk("b", Value::Null)],
            "'<' not supported between instances of 'int' and 'NoneType'",
        );
        err(
            vec![mk("a", Value::Null), mk("b", json!(5))],
            "'<' not supported between instances of 'NoneType' and 'int'",
        );
        err(
            vec![mk("a", Value::Null), mk("b", Value::Null)],
            "'<' not supported between instances of 'NoneType' and 'NoneType'",
        );
        err(
            vec![
                mk("a", json!(1)),
                mk("b", json!(2)),
                mk("c", Value::Null),
                mk("d", json!(4)),
            ],
            "'<' not supported between instances of 'NoneType' and 'int'",
        );
        err(
            vec![
                mk("a", json!(1)),
                mk("b", json!(2)),
                mk("c", json!(3)),
                mk("d", Value::Null),
            ],
            "'<' not supported between instances of 'int' and 'NoneType'",
        );
    }

    #[test]
    fn comments_skips_non_object_entries() {
        // The comments array may contain non-object entries (e.g. null, a string,
        // a number). The formatter must skip them rather than emit a degenerate
        // "@: \n\n" line. Only real object entries appear in the output.
        let data = json!({
            "success": true,
            "comments": [
                {"author": "alice", "text": "good", "likes": 5},
                null,
                "stray string",
                42,
                [],
                {"author": "bob", "text": "nice", "likes": 1},
            ],
        });
        let out = format_comments_for_context(&data, "https://yt/x");
        // alice is idx=0 -> "1. @alice"; bob is idx=5 -> "6. @bob".
        // (The enumeration index is NOT reset on skip — each array slot still
        // consumes an index, so the rendered number reflects the slot position.)
        assert!(out.contains("1. @alice [5 likes]: good\n\n"));
        assert!(out.contains("6. @bob [1 likes]: nice\n\n"));
        // No degenerate "@: " lines from the skipped entries.
        assert!(!out.contains("@: "));
        // The skipped slots (2-5) must not appear as rendered lines.
        assert!(!out.contains("2. @"));
        assert!(!out.contains("3. @"));
        assert!(!out.contains("4. @"));
        assert!(!out.contains("5. @"));
        assert!(out.ends_with("[END COMMENTS]\n"));
    }

    // -- Network fetchers: assert the FAILURE shape degrades correctly --------
    //
    // These exercise the fetchers WITHOUT requiring network success (CI is
    // offline / yt-dlp may be absent). We force the "yt-dlp not installed" /
    // spawn-NotFound branch deterministically by pointing the binary lookup at
    // an empty `PATH` and a temp `current_exe` dir with no `yt-dlp`, so
    // `find_ytdlp()` resolves to the bare `"yt-dlp"` and the spawn fails with
    // `NotFound`. (`PATH=""` -> `shutil_which` returns None.) We only assert the
    // degradation contract: on failure the formatters yield the unavailable
    // branch / empty string, never a fabricated transcript/comments.

    #[tokio::test]
    async fn transcript_failure_degrades_formatter() {
        // Empty PATH so `find_ytdlp()` can't resolve a real binary; the spawn of
        // the bare "yt-dlp" then fails -> the honest failure shape.
        let saved = std::env::var_os("PATH");
        std::env::set_var("PATH", "");
        let t = extract_transcript_async("https://yt/x", "definitely-not-a-real-id", 1).await;
        if let Some(p) = saved {
            std::env::set_var("PATH", p);
        } else {
            std::env::remove_var("PATH");
        }

        // Honest failure (never fabricated success): success==false, transcript null.
        assert_eq!(t["success"], json!(false));
        assert_eq!(t["transcript"], Value::Null);
        // The failure Value degrades the formatter to the unavailable branch.
        let out = format_transcript_for_context(&t, "https://yt/x", "", "");
        assert!(out.contains("Transcript unavailable"));
    }

    #[tokio::test]
    async fn comments_failure_degrades_formatter() {
        let saved = std::env::var_os("PATH");
        std::env::set_var("PATH", "");
        let c = fetch_youtube_comments("definitely-not-a-real-id", 25, 30).await;
        if let Some(p) = saved {
            std::env::set_var("PATH", p);
        } else {
            std::env::remove_var("PATH");
        }

        // Honest failure: success==false, comments == [].
        assert_eq!(c["success"], json!(false));
        assert_eq!(c["comments"], json!([]));
        // The failure Value degrades the formatter to the empty string.
        assert_eq!(format_comments_for_context(&c, "https://yt/x"), "");
    }
}
