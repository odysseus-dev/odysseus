// src/chat_handler.rs  <- src/chat_handler.py
//! Handler for chat endpoint operations.
//!
//! Faithful port of `ChatHandler`, the shared pre-processing/validation layer
//! sitting between the `/api/chat` + `/api/chat_stream` route handlers and the
//! streaming/LLM core. PORT_PARTIAL — most of the module is portable, but the
//! image-vision and PDF/attachment-assembly paths reach into collaborators that
//! land in their own translation passes.
//!
//! ## Web/DB collaborators
//!
//! `preprocess_message` and `handle_memory_command` are `async` (they fetch
//! YouTube data, build multimodal content, persist sessions); the whole module
//! pulls in the DB-backed `SessionManager` + `update_session_last_accessed`, the
//! `document_processor` free functions, and the (sibling-pass) `upload_handler`
//! surface. The crate has no cargo feature flags, so this module is always
//! compiled.
//!
//! ## Collaborator wiring (documented)
//!
//! Python's `ChatHandler.__init__` takes six already-constructed collaborators
//! and only ever touches them by duck-typed attribute/method access. The Rust
//! port wires each to the narrowest available surface:
//!
//! * `session_manager` — only `save_sessions()` is called here →
//!   concrete `Arc<`[`crate::core::session_manager::SessionManager`]`>`.
//! * `memory_manager` — `process_inline_memory_command` / `load` /
//!   `find_duplicates` / `add_entry` / `save` →
//!   concrete `Arc<`[`crate::src::memory::MemoryManager`]`>`.
//! * `preset_manager` — only `preset_manager.presets` (a `Dict[str, dict]`) is
//!   read. The full `PresetManager` is not ported, so the narrow surface is the
//!   [`PresetManager`] trait (one `presets()` accessor).
//! * `upload_handler` — `is_image_file(...)` is called here and the handler is
//!   forwarded into `build_user_content`. `upload_handler.py` is a sibling pass;
//!   the shared seam is `document_processor`'s
//!   [`crate::src::document_processor::UploadHandlerLike`] trait (reused, not
//!   re-declared), so `ChatHandler` is generic over the concrete handler type.
//! * `document_processor` — `build_user_content` + `analyze_image_with_vl_result`
//!   are the ported free functions in [`crate::src::document_processor`]. They
//!   are called directly (no `__init__` field; in Python these are module
//!   `import`s). `analyze_image_with_vl_result` is the REAL ported vision call
//!   (resolve_vl_model -> vl_chat/llm_call_async, with honest bracketed markers
//!   on disabled/no-model/all-fail) — this handler stores whatever `text` it
//!   returns without special-casing.
//! * `chat_processor` / `research_handler` — stored by `__init__` but **never
//!   used by any method in this file** (the route handlers use them directly).
//!   They are held as opaque [`Collaborator`] handles purely so the struct shape
//!   matches Python; nothing in this module calls into them.

use std::collections::HashMap;
use std::net::IpAddr;
use std::sync::{Arc, Mutex};

use once_cell::sync::Lazy;
use serde_json::{json, Map, Value};

use crate::core::models::{ChatMessage, Session};
use crate::core::session_manager::SessionManager;
use crate::error::PyResult;
use crate::pylog as logger;
use crate::routes::HttpException;
use crate::src::chat_helpers::extract_urls;
use crate::src::constants::{
    DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE, MAX_CONTEXT_MESSAGES, UPLOAD_DIR,
};
use crate::src::document_processor::{
    analyze_image_with_vl_result, build_user_content, UploadHandlerLike,
};
use crate::src::memory::MemoryManager;
use crate::src::settings::get_setting;
use crate::src::youtube_min::{
    extract_transcript_async, extract_youtube_id, fetch_youtube_comments,
    format_comments_for_context, format_transcript_for_context, is_youtube_url,
    YOUTUBE_INSTRUCTION_PROMPT,
};

// ---------------------------------------------------------------------------
// Duck-typed collaborator seams
// ---------------------------------------------------------------------------

/// The single capability `ChatHandler` needs from `preset_manager`:
/// `preset_manager.presets` — a `Dict[str, dict]` of `preset_id -> preset`.
///
/// `validate_and_extract_preset` reads it twice (`preset_id not in presets`,
/// then `presets[preset_id]`); both reduce to a map lookup. The full
/// `PresetManager` lands in its own pass and implements this trait.
pub trait PresetManager: Send + Sync {
    /// `self.preset_manager.presets` — the ordered preset registry.
    fn presets(&self) -> &Map<String, Value>;
}

/// Opaque handle for the collaborators `ChatHandler.__init__` stores but never
/// calls in this module (`chat_processor`, `research_handler`). Held only so the
/// struct shape mirrors Python; the route layer uses them directly.
pub trait Collaborator: Send + Sync {}

// ---------------------------------------------------------------------------
// Vision-model heuristics (module-level constants compiled once)
// ---------------------------------------------------------------------------

/// `_VISION_MODEL_KEYWORDS` (chat_helpers.py) — substrings that mark the main
/// model as natively vision-capable (so the image is passed inline rather than
/// VL-described). A missed match here silently drops the image (it gets swapped
/// for a text caption), so the list errs broad, especially for local models.
static VISION_KEYWORDS: &[&str] = &[
    // hosted
    "gpt-4o",
    "gpt-4.1",
    "gpt-4.5",
    "gpt-4-turbo",
    "gpt-4-vision",
    "claude-sonnet",
    "claude-opus",
    "claude-haiku",
    "gemini",
    // open / local
    "vision",
    "multimodal",
    "llava",
    "bakllava",
    "moondream",
    "pixtral",
    "minicpm",
    "internvl",
    "cogvlm",
    "qwen-vl",
    "qwen2-vl",
    "qwen3-vl",
    "qwen3vl",
    // multimodal families whose names don't contain "vision"/"vl" but DO accept
    // images (err-toward-True policy, issue #1274 / #124).
    "gemma-3",
    "gemma3",
    "gemma-4",
    "gemma4",
    "llama-4",
    "llama4",
    "mistral-small-3.1",
    "mistral-small3.1",
    "mistral-small-3.2",
    "mistral-small3.2",
    "phi-4",
    "phi4",
    // zhipu / glm (glm-4.5v, glm-4.6v, glm-5v-turbo, etc.)
    "glm-4.5v",
    "glm-4.6v",
    "glm-5v",
];

/// `_VISION_VL_RE = re.compile(r'(?<![a-z])vl(?![a-z])|vlm')` — catches the
/// "*-VL-*"/"*VL*" family not covered by a literal keyword above (e.g.
/// Qwen2.5-VL): a standalone `vl` token (not flanked by ASCII letters), plus
/// `vlm`. Rust's `regex` crate has no lookbehind, so the `(?<![a-z])vl(?![a-z])`
/// alternative is reproduced by [`vl_name_match`]; the `vlm` literal is a plain
/// `contains` check. (`Lazy<Regex>` is no longer needed.)
fn vl_name_match(model: &str) -> bool {
    if model.contains("vlm") {
        return true;
    }
    // `(?<![a-z])vl(?![a-z])` — every "vl" occurrence whose neighbouring chars
    // (when present) are not ASCII lowercase letters. The input is already
    // lowercased by the caller, matching Python's `m = model.lower()`.
    let bytes = model.as_bytes();
    let mut i = 0usize;
    while let Some(pos) = model[i..].find("vl") {
        let start = i + pos;
        let end = start + 2;
        let before_ok = start == 0 || !bytes[start - 1].is_ascii_lowercase();
        let after_ok = end >= bytes.len() || !bytes[end].is_ascii_lowercase();
        if before_ok && after_ok {
            return true;
        }
        i = start + 1;
    }
    false
}

/// `is_vision_model(model_name)` (chat_helpers.py) — best-effort name-based
/// check of whether a model can natively accept images. Errs toward `true`,
/// since a false negative drops the image entirely (issue #124).
fn is_vision_model(model_name: &str) -> bool {
    // `m = (model_name or "").lower()`
    let m = model_name.to_lowercase();
    if VISION_KEYWORDS.iter().any(|kw| m.contains(kw)) {
        return true;
    }
    vl_name_match(&m)
}

// ---------------------------------------------------------------------------
// LM Studio capability probe (chat_helpers.py: _probe_lmstudio_models /
// lmstudio_supports_vision / model_supports_vision)
// ---------------------------------------------------------------------------

/// `_PROVIDER_FINGERPRINT_TTL = 60.0` — how long a probe result is cached.
const PROVIDER_FINGERPRINT_TTL: f64 = 60.0;

/// `(host, port) -> (models | None, expiry)` — the LM Studio capability cache map.
type LmStudioCache = HashMap<(String, Option<u16>), (Option<Vec<Value>>, f64)>;

/// `_lmstudio_models_cache: dict` — `(host, port) -> (models | None, expiry)`.
/// `Some(list)` = LM Studio, `None` = not LM Studio / unreachable. Transient
/// errors are NOT cached (mirrors Python returning before the cache write).
static LMSTUDIO_MODELS_CACHE: Lazy<Mutex<LmStudioCache>> =
    Lazy::new(|| Mutex::new(HashMap::new()));

/// Module-level blocking client for the 1s probe (mirrors Python's per-call
/// `httpx.get(..., timeout=1.0)`; a shared client avoids per-call setup cost).
static PROBE_CLIENT: Lazy<reqwest::blocking::Client> = Lazy::new(|| {
    reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(1))
        .build()
        .unwrap_or_else(|_| reqwest::blocking::Client::new())
});

/// `_is_local_host(host)` — True for loopback/LAN/Tailscale hosts (never public
/// domains). `host` is the URL hostname (already without port).
fn is_local_host(host: &str) -> bool {
    // `host = (host or "").lower()`
    let host = host.to_lowercase();
    if host.is_empty() {
        return false;
    }
    // `if host in {"localhost", "host.docker.internal"} or host.endswith(".local"):`
    if host == "localhost" || host == "host.docker.internal" || host.ends_with(".local") {
        return true;
    }
    // `try: ip = ipaddress.ip_address(host) except ValueError: return "." not in host`
    let ip: IpAddr = match host.parse() {
        Ok(ip) => ip,
        Err(_) => return !host.contains('.'),
    };
    // `if ip.is_loopback or ip.is_private or ip.is_link_local: return True`
    if ip.is_loopback() || is_private(ip) || is_link_local(ip) {
        return true;
    }
    // `return ip in ipaddress.ip_network("100.64.0.0/10")` — Tailscale CGNAT.
    in_cgnat_100_64(ip)
}

/// `ip.is_private` — Python only flags the RFC1918 ranges (and unique-local for
/// v6). `Ipv4Addr::is_private` matches RFC1918; `Ipv6Addr` unique-local is the
/// `fc00::/7` block.
fn is_private(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(a) => a.is_private(),
        IpAddr::V6(a) => (a.segments()[0] & 0xfe00) == 0xfc00,
    }
}

fn is_link_local(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(a) => a.is_link_local(),
        IpAddr::V6(a) => (a.segments()[0] & 0xffc0) == 0xfe80,
    }
}

/// `ip in ipaddress.ip_network("100.64.0.0/10")` (IPv4 CGNAT / Tailscale).
fn in_cgnat_100_64(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(a) => {
            let o = a.octets();
            o[0] == 100 && (64..=127).contains(&o[1])
        }
        IpAddr::V6(_) => false,
    }
}

/// `_probe_lmstudio_models(url)` — return LM Studio's native `/api/v1/models`
/// list, or `None` when the endpoint isn't LM Studio or is unreachable
/// (short-TTL cached; transient errors uncached).
fn probe_lmstudio_models(url: &str) -> Option<Vec<Value>> {
    // `parsed = urlparse(url); host = parsed.hostname or ""`
    let parsed = url::Url::parse(url).ok();
    let host = parsed
        .as_ref()
        .and_then(|p| p.host_str())
        .unwrap_or("")
        .to_string();
    let port = parsed.as_ref().and_then(|p| p.port());
    let scheme = parsed
        .as_ref()
        .map(|p| p.scheme())
        .filter(|s| !s.is_empty())
        .unwrap_or("http")
        .to_string();

    let key = (host.clone(), port);
    let now = crate::pytime::time();
    // `cached = _lmstudio_models_cache.get(key); if cached and cached[1] > now: return cached[0]`
    if let Ok(cache) = LMSTUDIO_MODELS_CACHE.lock() {
        if let Some((models, expiry)) = cache.get(&key) {
            if *expiry > now {
                return models.clone();
            }
        }
    }

    // `authority = host if port is None else f"{host}:{port}"`
    let authority = match port {
        None => host.clone(),
        Some(p) => format!("{host}:{p}"),
    };
    let probe_url = format!("{scheme}://{authority}/api/v1/models");

    // `try: r = httpx.get(probe_url, timeout=1.0) except Exception: return None`
    let resp = match PROBE_CLIENT.get(&probe_url).send() {
        Ok(r) => r,
        Err(_) => return None,
    };
    // `data = r.json() if r.is_success else {}` (then `except Exception: data = {}`)
    let data: Value = if resp.status().is_success() {
        resp.json().unwrap_or_else(|_| json!({}))
    } else {
        json!({})
    };

    // `models = data.get("models")`
    let models_val = data.get("models");
    // `valid = isinstance(models, list) and bool(models) and isinstance(models[0], dict)
    //           and "key" in models[0] and "architecture" in models[0]`
    let models: Option<Vec<Value>> = match models_val.and_then(|v| v.as_array()) {
        Some(arr) if !arr.is_empty() => {
            let first = &arr[0];
            if first.is_object()
                && first.get("key").is_some()
                && first.get("architecture").is_some()
            {
                Some(arr.clone())
            } else {
                None
            }
        }
        _ => None,
    };
    // `_lmstudio_models_cache[key] = (models, now + _PROVIDER_FINGERPRINT_TTL)`
    if let Ok(mut cache) = LMSTUDIO_MODELS_CACHE.lock() {
        cache.insert(key, (models.clone(), now + PROVIDER_FINGERPRINT_TTL));
    }
    models
}

/// `lmstudio_supports_vision(url, model)` — read `model`'s `capabilities.vision`
/// flag from LM Studio, or `None` when the endpoint isn't LM Studio or doesn't
/// report it (so callers fall back).
fn lmstudio_supports_vision(url: &str, model: &str) -> Option<bool> {
    // `if not model: return None`
    if model.is_empty() {
        return None;
    }
    // `if not _is_local_host(urlparse(url).hostname): return None`
    let host = url::Url::parse(url)
        .ok()
        .and_then(|p| p.host_str().map(str::to_string))
        .unwrap_or_default();
    if !is_local_host(&host) {
        return None;
    }
    // `models = _probe_lmstudio_models(url); if not models: return None`
    let models = probe_lmstudio_models(url)?;
    if models.is_empty() {
        return None;
    }
    // `want = model.strip().lower()`
    let want = model.trim().to_lowercase();
    for m in &models {
        // `if not isinstance(m, dict): continue`
        let obj = match m.as_object() {
            Some(o) => o,
            None => continue,
        };
        // `names = {str(m.get("key","")).lower(), str(m.get("display_name","")).lower()}`
        let key_name = obj.get("key").and_then(|v| v.as_str()).unwrap_or("").to_lowercase();
        let disp_name = obj
            .get("display_name")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_lowercase();
        // `if want in names:`
        if want == key_name || want == disp_name {
            // `caps = m.get("capabilities")`
            if let Some(caps) = obj.get("capabilities").and_then(|v| v.as_object()) {
                if let Some(vision) = caps.get("vision") {
                    // `return bool(caps.get("vision"))`
                    return Some(truthy(vision));
                }
            }
            // `return None` — model found, but no vision capability reported.
            return None;
        }
    }
    None
}

/// `model_supports_vision(model_name, endpoint_url="")` — whether a model accepts
/// images, using the endpoint's reported capability when available (LM Studio)
/// and falling back to name-based detection otherwise.
///
/// BLOCKING (the LM Studio probe is a synchronous HTTP GET); the async
/// `preprocess_message` invokes it via `tokio::task::spawn_blocking`, the
/// faithful analogue of Python's `asyncio.to_thread(model_supports_vision, ...)`.
pub fn model_supports_vision(model_name: &str, endpoint_url: &str) -> bool {
    // `if endpoint_url:`
    if !endpoint_url.is_empty() {
        // `try: advertised = lmstudio_supports_vision(...) except Exception: advertised = None`
        let advertised = lmstudio_supports_vision(endpoint_url, model_name);
        // `if advertised is not None: return advertised`
        if let Some(v) = advertised {
            return v;
        }
    }
    // `return is_vision_model(model_name)`
    is_vision_model(model_name)
}

// ---------------------------------------------------------------------------
// ChatHandler
// ---------------------------------------------------------------------------

/// Handles chat operations for both streaming and non-streaming endpoints.
///
/// Generic over the concrete `upload_handler` type `U` (any
/// [`UploadHandlerLike`], which now exposes the owner-aware `resolve_upload`) so
/// it can forward the handler into the generic `build_user_content` without a
/// trait-object `Sized` wrapper. `U` is shared across `async` boundaries, hence
/// the `Send + Sync + 'static` bound.
pub struct ChatHandler<U: UploadHandlerLike + Send + Sync + 'static> {
    pub session_manager: Arc<SessionManager>,
    pub memory_manager: Arc<MemoryManager>,
    /// Stored to mirror `__init__`; unused by this module's methods.
    pub chat_processor: Arc<dyn Collaborator>,
    /// Stored to mirror `__init__`; unused by this module's methods.
    pub research_handler: Arc<dyn Collaborator>,
    pub preset_manager: Arc<dyn PresetManager>,
    pub upload_handler: Arc<U>,
}

impl<U: UploadHandlerLike + Send + Sync + 'static> ChatHandler<U> {
    /// `__init__(self, session_manager, memory_manager, chat_processor,
    /// research_handler, preset_manager, upload_handler)`.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        session_manager: Arc<SessionManager>,
        memory_manager: Arc<MemoryManager>,
        chat_processor: Arc<dyn Collaborator>,
        research_handler: Arc<dyn Collaborator>,
        preset_manager: Arc<dyn PresetManager>,
        upload_handler: Arc<U>,
    ) -> Self {
        ChatHandler {
            session_manager,
            memory_manager,
            chat_processor,
            research_handler,
            preset_manager,
            upload_handler,
        }
    }

    // ------------------------------------------------------------------
    // Preset helpers
    // ------------------------------------------------------------------

    /// Returns `(temperature, max_tokens, preset_system_prompt, character_name)`.
    ///
    /// `raise HTTPException(400, f"Invalid preset_id: {preset_id}")` maps to
    /// `Err(HttpException::new(400, ...))`.
    pub fn validate_and_extract_preset(
        &self,
        preset_id: Option<&str>,
    ) -> Result<(f64, i64, Option<String>, String), HttpException> {
        let presets = self.preset_manager.presets();

        // `if preset_id and preset_id not in self.preset_manager.presets:`
        // — Python `and` short-circuits on a falsy (None/empty) preset_id.
        if let Some(pid) = preset_id.filter(|p| !p.is_empty()) {
            if !presets.contains_key(pid) {
                return Err(HttpException::new(400, format!("Invalid preset_id: {pid}")));
            }
        }

        let mut temperature = DEFAULT_TEMPERATURE;
        let mut max_tokens = DEFAULT_MAX_TOKENS;
        let mut preset_system_prompt: Option<String> = None;
        let mut character_name = String::new();

        // `if preset_id and preset_id in self.preset_manager.presets:`
        if let Some(pid) = preset_id.filter(|p| !p.is_empty()) {
            if let Some(preset) = presets.get(pid) {
                // `if preset.get("enabled") is False:` — *only* an explicit
                // JSON `false` (missing / null / truthy all pass through).
                if matches!(preset.get("enabled"), Some(Value::Bool(false))) {
                    logger::info(&format!("Preset {pid} is disabled, using defaults"));
                    return Ok((temperature, max_tokens, preset_system_prompt, character_name));
                }
                // `if preset.get("system_prompt"):` — truthy string only.
                if let Some(sp) = preset.get("system_prompt").and_then(|v| v.as_str()) {
                    if !sp.is_empty() {
                        preset_system_prompt = Some(sp.to_string());
                    }
                }
                // `character_name = preset.get("character_name", "")`
                character_name = preset
                    .get("character_name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                if !character_name.is_empty() {
                    let name_line = format!("Your name is {character_name}.");
                    preset_system_prompt = Some(match preset_system_prompt {
                        Some(sp) => format!("{name_line} {sp}"),
                        None => name_line,
                    });
                }
                // `if "temperature" in preset:` — present key wins. Python copies
                // the value verbatim; the Rust default is kept if the stored value
                // is not numeric (the only sane coercion — documented).
                if let Some(t) = preset.get("temperature") {
                    if let Some(t) = t.as_f64() {
                        temperature = t;
                    }
                }
                if let Some(m) = preset.get("max_tokens") {
                    if let Some(m) = m.as_i64() {
                        max_tokens = m;
                    }
                }
            }
        }

        logger::info(&format!(
            "Preset {}: temp={temperature}, max_tokens={max_tokens}",
            preset_id.unwrap_or("None")
        ));
        Ok((temperature, max_tokens, preset_system_prompt, character_name))
    }

    /// CoT enhancement disabled — modern models reason natively.
    pub fn enhance_message_if_needed(&self, message: &str) -> String {
        message.to_string()
    }

    // ------------------------------------------------------------------
    // Preprocessing — shared between /api/chat and /api/chat_stream
    // ------------------------------------------------------------------

    /// Common preprocessing for both chat endpoints.
    ///
    /// Returns `(enhanced_message, user_content, text_for_context,
    /// youtube_transcripts, attachment_meta)`.
    ///
    /// If `auto_opened_docs` is provided, server-side document auto-creation
    /// (e.g. from an attached fillable PDF) appends entries describing the new
    /// doc so the caller can announce it to the frontend before streaming.
    ///
    /// PARITY NOTES:
    /// * Python fetches transcript + comments concurrently via
    ///   `asyncio.gather(transcript_task, comments_task)`. Both Rust youtube_min
    ///   fetchers are now REAL async, yt-dlp-backed fetchers (transcript via
    ///   json3 subtitles, comments via the `--write-comments`/`--dump-json`
    ///   subprocess), so they are run CONCURRENTLY here with `tokio::join!` — the
    ///   faithful analogue of `asyncio.gather`. Both the *observable result* and
    ///   the concurrency now match Python (on any fetch failure the formatters
    ///   still degrade to the "Transcript unavailable"/"no comments" branches).
    /// * `analyze_image_with_vl_result` is the honest vision stub in
    ///   `document_processor`; its returned text is folded in verbatim, exactly
    ///   as Python folds in the real description.
    // The two `collapse_text_parts` arms are identical on purpose: they mirror the
    // deliberate `if not vision_enabled .. elif not main_is_vision ..` if/elif in
    // chat_handler.py:255-266, whose branch bodies are byte-for-byte the same.
    #[allow(clippy::if_same_then_else)]
    pub async fn preprocess_message(
        &self,
        message: &str,
        att_ids: &[String],
        sess: &Session,
        auto_opened_docs: Option<&mut Vec<Value>>,
    ) -> (String, Value, String, Vec<String>, Vec<Value>) {
        let mut enhanced_message = message.to_string();
        let mut attachment_meta: Vec<Value> = Vec::new();

        // Extract URLs and process YouTube transcripts.
        let urls = extract_urls(&enhanced_message);
        let mut youtube_transcripts: Vec<String> = Vec::new();

        let mut has_youtube = false;
        for url in &urls {
            if is_youtube_url(url) {
                let video_id = match extract_youtube_id(url) {
                    // `if not video_id: continue` — None *or* empty string skip.
                    Some(v) if !v.is_empty() => v,
                    _ => continue,
                };
                has_youtube = true;
                logger::info(&format!("Processing YouTube URL: {url}"));
                // Fetch transcript and comments CONCURRENTLY — `tokio::join!`
                // mirrors Python's `asyncio.gather(transcript_task, comments_task)`:
                // both real async yt-dlp-backed fetchers are polled on this task at
                // once. Arg defaults 3 / 25 / 30 come from the Python fn signatures
                // (max_retries=3, max_comments=25, timeout=30; the Python call sites
                // pass none).
                let (transcript_data, comments_data) = tokio::join!(
                    extract_transcript_async(url, &video_id, 3),
                    fetch_youtube_comments(&video_id, 25, 30)
                );
                // Extract title/channel from comments metadata.
                let title = comments_data
                    .get("title")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let channel = comments_data
                    .get("channel")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                youtube_transcripts.push(format_transcript_for_context(
                    &transcript_data,
                    url,
                    title,
                    channel,
                ));
                let comments_ctx = format_comments_for_context(&comments_data, url);
                if !comments_ctx.is_empty() {
                    youtube_transcripts.push(comments_ctx);
                }
            }
        }

        // Inject instruction prompt so the LLM gives a structured breakdown.
        if has_youtube {
            // `youtube_transcripts.insert(0, YOUTUBE_INSTRUCTION_PROMPT)`
            youtube_transcripts.insert(0, YOUTUBE_INSTRUCTION_PROMPT.to_string());
        }

        // Analyze images — skip if vision disabled, or if main model is
        // vision-capable. `get_setting("vision_enabled", True)`.
        let vision_enabled = truthy(&get_setting("vision_enabled", json!(true)));

        // `main_is_vision = await asyncio.to_thread(model_supports_vision,
        //   sess.model or "", getattr(sess, "endpoint_url", "") or "")` — the
        // LM Studio capability probe is a blocking HTTP GET, so run it on a
        // blocking thread (the faithful analogue of `asyncio.to_thread`).
        let model = sess.model.clone();
        let endpoint_url = sess.endpoint_url.clone();
        let main_is_vision = tokio::task::spawn_blocking(move || {
            model_supports_vision(&model, &endpoint_url)
        })
        .await
        .unwrap_or_else(|_| model_supports_vision(&sess.model, &sess.endpoint_url));

        // Resolve uploads once with the session owner. Attachment IDs are
        // bearer-like references; never trust them without an owner check.
        // `owner = getattr(sess, "owner", None)`.
        let owner: Option<&str> = sess.owner.as_deref();
        let mut files_by_id: HashMap<String, Map<String, Value>> = HashMap::new();
        if !att_ids.is_empty() {
            // `for att_id in att_ids: fi = self.upload_handler.resolve_upload(att_id, owner=owner)`
            for att_id in att_ids {
                if let Some(fi) = self.upload_handler.resolve_upload(att_id, owner) {
                    files_by_id.insert(att_id.clone(), fi);
                }
            }

            for att_id in att_ids {
                if let Some(fi) = files_by_id.get(att_id) {
                    let mut m = Map::new();
                    // `"id": fi["id"]` — resolve_upload always sets an id.
                    m.insert("id".to_string(), fi.get("id").cloned().unwrap_or(Value::Null));
                    // `"name": fi.get("name") or fi.get("original_name") or fi["id"]`
                    let name = fi
                        .get("name")
                        .filter(|v| truthy(v))
                        .or_else(|| fi.get("original_name").filter(|v| truthy(v)))
                        .or_else(|| fi.get("id"))
                        .cloned()
                        .unwrap_or(Value::Null);
                    m.insert("name".to_string(), name);
                    m.insert(
                        "mime".to_string(),
                        fi.get("mime").cloned().unwrap_or_else(|| json!("")),
                    );
                    m.insert(
                        "size".to_string(),
                        fi.get("size").cloned().unwrap_or_else(|| json!(0)),
                    );
                    // `fi.get("width")` / `fi.get("height")` -> None becomes Null.
                    m.insert(
                        "width".to_string(),
                        fi.get("width").cloned().unwrap_or(Value::Null),
                    );
                    m.insert(
                        "height".to_string(),
                        fi.get("height").cloned().unwrap_or(Value::Null),
                    );
                    attachment_meta.push(Value::Object(m));
                }
            }
        }

        if !att_ids.is_empty() && vision_enabled {
            // `meta_by_id = {m["id"]: m for m in attachment_meta}` — the Python
            // aliasing mutates the same dicts that live in `attachment_meta`; the
            // owned Rust list is mutated in place by id-lookup (see `set_meta`).
            for att_id in att_ids {
                // Clone the file_info out so the borrow of `files_by_id` ends
                // before we mutate `attachment_meta`.
                let file_info = match files_by_id.get(att_id) {
                    Some(fi) => fi.clone(),
                    None => continue,
                };
                // `file_info['name']` — resolve_upload always sets `name`, but
                // fall back name -> original_name -> id to mirror the meta naming.
                let name = file_info
                    .get("name")
                    .and_then(|v| v.as_str())
                    .filter(|s| !s.is_empty())
                    .or_else(|| file_info.get("original_name").and_then(|v| v.as_str()).filter(|s| !s.is_empty()))
                    .or_else(|| file_info.get("id").and_then(|v| v.as_str()))
                    .unwrap_or("")
                    .to_string();
                let name = name.as_str();
                // `file_info.get("mime", "")` — default to empty string. The
                // UploadHandlerLike surface takes `content_type: &str`.
                let mime = file_info.get("mime").and_then(|v| v.as_str()).unwrap_or("");
                if !self.upload_handler.is_image_file(name, mime) {
                    continue;
                }

                let vision_dir = crate::pyos::path::join(&UPLOAD_DIR, ".vision");
                let vcache = crate::pyos::path::join(&vision_dir, &format!("{att_id}.txt"));

                if main_is_vision {
                    // Main model can see images — just note it; image is passed
                    // via build_user_content.
                    enhanced_message = format!("{enhanced_message}\n\n[Image attached: {name}]");
                    set_meta(&mut attachment_meta, att_id, "vision_model", json!(sess.model));
                    // Fold in a user-corrected caption/OCR if one exists.
                    if crate::pyos::path::exists(&vcache) {
                        // `except Exception: pass`
                        if let Ok(raw) = std::fs::read_to_string(&vcache) {
                            let vtext = raw.trim();
                            if !vtext.is_empty() {
                                enhanced_message += &format!(
                                    "\n[User-corrected caption / OCR for this image — treat as authoritative]:\n{vtext}"
                                );
                                set_meta(&mut attachment_meta, att_id, "vision", json!(vtext));
                            }
                        }
                    }
                } else {
                    // Main model is text-only — use VL model for description.
                    // Prefer the cached/user-edited text in .vision/{id}.txt so a
                    // manual correction overrides what the VL model would say.
                    let mut vl_desc: Option<String> = None;
                    // `vl_model = get_setting("vision_model", "") or ""`
                    let mut vl_model = get_setting("vision_model", json!(""))
                        .as_str()
                        .unwrap_or("")
                        .to_string();
                    if crate::pyos::path::exists(&vcache) {
                        // `except Exception: vl_desc = None`
                        if let Ok(raw) = std::fs::read_to_string(&vcache) {
                            let cached_desc = raw.trim();
                            // `if cached_desc and not cached_desc.startswith("["):`
                            // — honest VL text only; skip bracketed status markers
                            // (e.g. "[Vision disabled]") so they don't poison the
                            // description.
                            if !cached_desc.is_empty() && !cached_desc.starts_with('[') {
                                vl_desc = Some(cached_desc.to_string());
                            }
                        }
                    }
                    // `if not vl_desc:` — None *or* empty string re-runs the VL.
                    if vl_desc.as_deref().map(str::is_empty).unwrap_or(true) {
                        let path = file_info
                            .get("path")
                            .and_then(|v| v.as_str())
                            .unwrap_or("");
                        let vl_result = analyze_image_with_vl_result(path);
                        let desc = vl_result
                            .get("text")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_string();
                        vl_model = vl_result
                            .get("model")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_string();
                        // `if vl_desc and not vl_desc.startswith("["):` — only cache
                        // an honest description; never persist a bracketed marker.
                        // `os.makedirs(.vision, exist_ok=True); write(vl_desc)` — all
                        // under `except Exception: pass`.
                        if !desc.is_empty() && !desc.starts_with('[') && crate::pyos::makedirs(&vision_dir, true).is_ok() {
                            let _ = std::fs::write(&vcache, &desc);
                        }
                        vl_desc = Some(desc);
                    }
                    let vl_desc_str = vl_desc.unwrap_or_default();
                    enhanced_message =
                        format!("{enhanced_message}\n\n[Image: {name}]\n{vl_desc_str}");
                    set_meta(&mut attachment_meta, att_id, "vision", json!(vl_desc_str));
                    set_meta(&mut attachment_meta, att_id, "vision_model", json!(vl_model));
                }
            }
        }

        // `resolved_uploads=files_by_id` — build_user_content wants a
        // `Map<String, Value>` whose values are the upload-metadata JSON objects,
        // while our `files_by_id` is a `HashMap<String, Map<String, Value>>`.
        // Re-key it into the expected shape (each value wrapped as `Value::Object`).
        let resolved_uploads: Map<String, Value> = files_by_id
            .iter()
            .map(|(k, v)| (k.clone(), Value::Object(v.clone())))
            .collect();
        let mut user_content = build_user_content(
            &enhanced_message,
            att_ids,
            &UPLOAD_DIR,
            self.upload_handler.as_ref(),
            // `getattr(sess, "id", None)` — Session always has an id.
            Some(sess.id.as_str()),
            auto_opened_docs,
            // `owner=owner, resolved_uploads=files_by_id` — let build_user_content
            // reuse the already owner-checked resolutions instead of re-reading.
            owner,
            &resolved_uploads,
        );

        // Strip image_url entries for text-only models (VL description is already
        // in the text). Both branches collapse a content list down to its text.
        if !vision_enabled && user_content.is_array() {
            user_content = collapse_text_parts(&user_content, &enhanced_message);
        } else if !main_is_vision && user_content.is_array() {
            user_content = collapse_text_parts(&user_content, &enhanced_message);
        }

        // Extract text portion for naming / context.
        let text_for_context = if let Some(items) = user_content.as_array() {
            // `next((item["text"] for item in user_content if item.get("type") == "text"), enhanced_message)`
            items
                .iter()
                .find(|item| item.get("type").and_then(|v| v.as_str()) == Some("text"))
                .and_then(|item| item.get("text").and_then(|v| v.as_str()))
                .map(str::to_string)
                .unwrap_or_else(|| enhanced_message.clone())
        } else {
            // `text_for_context = user_content` (a str).
            user_content.as_str().unwrap_or("").to_string()
        };

        (
            enhanced_message,
            user_content,
            text_for_context,
            youtube_transcripts,
            attachment_meta,
        )
    }

    // ------------------------------------------------------------------
    // Session helpers
    // ------------------------------------------------------------------

    /// `if not session.name:` derive a name from the first five words.
    pub fn update_session_name_if_needed(&self, session: &mut Session, message: &str) {
        if session.name.is_empty() {
            // `" ".join(message.split()[:5])` — split on arbitrary whitespace runs.
            let derived = message
                .split_whitespace()
                .take(5)
                .collect::<Vec<_>>()
                .join(" ");
            session.name = if !derived.is_empty() {
                format!("Chat: {derived}")
            } else {
                "Chat".to_string()
            };
        }
    }

    /// Trim history to the most recent `MAX_CONTEXT_MESSAGES` messages.
    pub fn trim_history_if_needed(&self, session: &mut Session) {
        let len = session.history.len() as i64;
        if len > MAX_CONTEXT_MESSAGES {
            // `session.history = session.history[-MAX_CONTEXT_MESSAGES:]`
            let keep = MAX_CONTEXT_MESSAGES as usize;
            let start = session.history.len() - keep;
            session.history.drain(0..start);
        }
    }

    /// Process inline memory commands. Returns the response string or `None`.
    ///
    /// `session` is `&mut` because `session.add_message(...)` mutates history
    /// (and persists via the global session-manager handle, mirroring Python
    /// where `session` is the live stored object).
    pub async fn handle_memory_command(
        &self,
        session: &mut Session,
        message: &str,
    ) -> PyResult<Option<String>> {
        let (is_memory_cmd, memory_text) =
            self.memory_manager.process_inline_memory_command(message);
        // `if is_memory_cmd and memory_text:` — non-empty text required.
        if is_memory_cmd && !memory_text.is_empty() {
            // `mem = self.memory_manager.load()` (no owner filter)
            let mut mem = self.memory_manager.load(None);
            // `if not self.memory_manager.find_duplicates(memory_text, mem):`
            if self
                .memory_manager
                .find_duplicates(&memory_text, Some(&mem))
                .is_empty()
            {
                // `new_entry = self.memory_manager.add_entry(memory_text)`
                // — Python defaults source="user", category="fact", owner=None.
                let new_entry =
                    self.memory_manager
                        .add_entry(&memory_text, "user", "fact", None)?;
                mem.push(new_entry);
                self.memory_manager.save(&mut mem)?;
            }

            session.add_message(ChatMessage::new("user", message, None));
            session.add_message(ChatMessage::new(
                "assistant",
                format!("Saved to memory: {memory_text}"),
                None,
            ));

            // `from src.database import update_session_last_accessed`
            crate::core::database::update_session_last_accessed(&session.id);
            self.session_manager.save_sessions();
            return Ok(Some(format!("Saved to memory: {memory_text}")));
        }
        Ok(None)
    }
}

// ---------------------------------------------------------------------------
// Free helpers
// ---------------------------------------------------------------------------

/// Python truthiness of a setting value (`get_setting(...)` returns a bool here
/// but a hand-edited settings.json could hold any JSON; treat it the way Python
/// `if vision_enabled:` would).
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Mutate the `attachment_meta` entry whose `id` matches `att_id`, setting
/// `key=value`. Reproduces the Python `_m = meta_by_id.get(att_id); if _m is not
/// None: _m[key] = value` — `meta_by_id` aliases the dicts in `attachment_meta`,
/// so the write lands on the list entry.
fn set_meta(attachment_meta: &mut [Value], att_id: &str, key: &str, value: Value) {
    for m in attachment_meta.iter_mut() {
        if m.get("id").and_then(|v| v.as_str()) == Some(att_id) {
            if let Some(obj) = m.as_object_mut() {
                obj.insert(key.to_string(), value);
            }
            return;
        }
    }
}

/// Collapse a content-block list to its concatenated text (the body shared by
/// the two `isinstance(user_content, list)` strip branches):
///
/// ```text
/// text_parts = [item["text"] for item in user_content
///               if isinstance(item, dict) and item["type"] == "text"]
/// user_content = "\n".join(text_parts).strip() if text_parts else enhanced_message
/// ```
fn collapse_text_parts(user_content: &Value, enhanced_message: &str) -> Value {
    let items = match user_content.as_array() {
        Some(a) => a,
        None => return user_content.clone(),
    };
    let text_parts: Vec<&str> = items
        .iter()
        .filter(|item| item.get("type").and_then(|v| v.as_str()) == Some("text"))
        .map(|item| item.get("text").and_then(|v| v.as_str()).unwrap_or(""))
        .collect();
    if text_parts.is_empty() {
        // `else enhanced_message`
        Value::String(enhanced_message.to_string())
    } else {
        // `"\n".join(text_parts).strip()`
        Value::String(text_parts.join("\n").trim().to_string())
    }
}

#[cfg(test)]
mod tests {
    // Parity checks against the live Python (`src.chat_handler`).
    use super::*;

    // --- test doubles for the duck-typed collaborators ---

    struct StubPresets(Map<String, Value>);
    impl PresetManager for StubPresets {
        fn presets(&self) -> &Map<String, Value> {
            &self.0
        }
    }

    /// A minimal `UploadHandlerLike` for the preset/name/trim tests (none of
    /// them exercise it; only the multimodal path does, which is integration-
    /// tested through the real `document_processor`).
    struct StubUpload;
    impl UploadHandlerLike for StubUpload {
        fn validate_upload_id(&self, _upload_id: &str) -> bool {
            false
        }
        fn inside_base_dir(&self, _path: &str) -> bool {
            false
        }
        fn is_image_file(&self, filename: &str, content_type: &str) -> bool {
            let lower = filename.to_lowercase();
            let by_ext = [".png", ".jpg", ".jpeg", ".webp", ".gif"]
                .iter()
                .any(|e| lower.ends_with(e));
            let by_mime = matches!(
                content_type,
                "image/png" | "image/jpeg" | "image/jpg" | "image/webp" | "image/gif"
            );
            by_ext || by_mime
        }
        fn is_audio_file(&self, _filename: &str, _content_type: &str) -> bool {
            false
        }
        fn is_document_file(&self, _filename: &str, _content_type: &str) -> bool {
            false
        }
        // `resolve_upload` uses the trait's default (`None`); none of the
        // preset/name/trim tests resolve attachments, and the multimodal path is
        // integration-tested through the real `UploadHandler`.
    }

    struct StubCollab;
    impl Collaborator for StubCollab {}

    fn handler(presets: Map<String, Value>) -> ChatHandler<StubUpload> {
        ChatHandler::new(
            Arc::new(SessionManager::new()),
            Arc::new(MemoryManager::new(&std::env::temp_dir().to_string_lossy()).unwrap()),
            Arc::new(StubCollab),
            Arc::new(StubCollab),
            Arc::new(StubPresets(presets)),
            Arc::new(StubUpload),
        )
    }

    #[test]
    fn preset_defaults_when_none() {
        let h = handler(Map::new());
        let (t, mt, sp, cn) = h.validate_and_extract_preset(None).unwrap();
        assert_eq!(t, DEFAULT_TEMPERATURE);
        assert_eq!(mt, DEFAULT_MAX_TOKENS);
        assert!(sp.is_none());
        assert_eq!(cn, "");
    }

    #[test]
    fn preset_invalid_id_is_400() {
        let h = handler(Map::new());
        let err = h.validate_and_extract_preset(Some("nope")).unwrap_err();
        assert_eq!(err.status_code, 400);
        assert_eq!(err.detail, "Invalid preset_id: nope");
    }

    #[test]
    fn preset_disabled_returns_defaults() {
        let mut presets = Map::new();
        presets.insert(
            "p1".to_string(),
            json!({"enabled": false, "system_prompt": "ignored", "temperature": 0.2}),
        );
        let h = handler(presets);
        let (t, mt, sp, cn) = h.validate_and_extract_preset(Some("p1")).unwrap();
        // Disabled preset short-circuits to defaults, ignoring its fields.
        assert_eq!(t, DEFAULT_TEMPERATURE);
        assert_eq!(mt, DEFAULT_MAX_TOKENS);
        assert!(sp.is_none());
        assert_eq!(cn, "");
    }

    #[test]
    fn preset_character_name_prefix() {
        let mut presets = Map::new();
        presets.insert(
            "p1".to_string(),
            json!({"system_prompt": "Be helpful.", "character_name": "Ada", "temperature": 0.5, "max_tokens": 123}),
        );
        let h = handler(presets);
        let (t, mt, sp, cn) = h.validate_and_extract_preset(Some("p1")).unwrap();
        assert_eq!(cn, "Ada");
        assert_eq!(sp.as_deref(), Some("Your name is Ada. Be helpful."));
        assert_eq!(t, 0.5);
        assert_eq!(mt, 123);
    }

    #[test]
    fn preset_character_name_only() {
        let mut presets = Map::new();
        presets.insert("p1".to_string(), json!({"character_name": "Ada"}));
        let h = handler(presets);
        let (_, _, sp, cn) = h.validate_and_extract_preset(Some("p1")).unwrap();
        assert_eq!(cn, "Ada");
        // No system_prompt -> the name line *is* the prompt.
        assert_eq!(sp.as_deref(), Some("Your name is Ada."));
    }

    #[test]
    fn empty_preset_id_is_treated_as_none() {
        // `if preset_id and ...` — empty string is falsy, so no lookup/raise.
        let h = handler(Map::new());
        let r = h.validate_and_extract_preset(Some(""));
        assert!(r.is_ok());
    }

    #[test]
    fn enhance_is_identity() {
        let h = handler(Map::new());
        assert_eq!(h.enhance_message_if_needed("hello"), "hello");
    }

    #[test]
    fn update_name_from_first_five_words() {
        let h = handler(Map::new());
        let mut s = Session::default();
        h.update_session_name_if_needed(&mut s, "  the quick brown fox jumps over the lazy dog ");
        assert_eq!(s.name, "Chat: the quick brown fox jumps");
    }

    #[test]
    fn update_name_blank_message() {
        let h = handler(Map::new());
        let mut s = Session::default();
        h.update_session_name_if_needed(&mut s, "    ");
        assert_eq!(s.name, "Chat");
    }

    #[test]
    fn update_name_noop_when_present() {
        let h = handler(Map::new());
        let mut s = Session {
            name: "Existing".to_string(),
            ..Session::default()
        };
        h.update_session_name_if_needed(&mut s, "hello world");
        assert_eq!(s.name, "Existing");
    }

    #[test]
    fn trim_keeps_last_n() {
        let h = handler(Map::new());
        let mut s = Session::default();
        for i in 0..(MAX_CONTEXT_MESSAGES + 10) {
            s.history
                .push(ChatMessage::new("user", format!("m{i}"), None));
        }
        h.trim_history_if_needed(&mut s);
        assert_eq!(s.history.len() as i64, MAX_CONTEXT_MESSAGES);
        // The kept window is the *tail*.
        assert_eq!(s.history.first().unwrap().content, "m10");
        assert_eq!(
            s.history.last().unwrap().content,
            format!("m{}", MAX_CONTEXT_MESSAGES + 9)
        );
    }

    #[test]
    fn trim_noop_when_at_or_below_limit() {
        let h = handler(Map::new());
        let mut s = Session::default();
        for i in 0..MAX_CONTEXT_MESSAGES {
            s.history
                .push(ChatMessage::new("user", format!("m{i}"), None));
        }
        h.trim_history_if_needed(&mut s);
        assert_eq!(s.history.len() as i64, MAX_CONTEXT_MESSAGES);
    }

    #[test]
    fn collapse_text_parts_joins_and_strips() {
        // join(" hello ", "world ") = " hello \nworld " then .strip() -> "hello \nworld"
        let content = json!([
            {"type": "text", "text": " hello "},
            {"type": "image_url", "image_url": {"url": "data:..."}},
            {"type": "text", "text": "world "},
        ]);
        let out = collapse_text_parts(&content, "fallback");
        assert_eq!(out.as_str().unwrap(), "hello \nworld");
    }

    #[test]
    fn collapse_text_parts_empty_falls_back() {
        let content = json!([{"type": "image_url", "image_url": {"url": "x"}}]);
        let out = collapse_text_parts(&content, "the-fallback");
        assert_eq!(out.as_str().unwrap(), "the-fallback");
    }

    #[test]
    fn set_meta_writes_matching_entry() {
        let mut meta = vec![json!({"id": "a", "name": "x"}), json!({"id": "b"})];
        set_meta(&mut meta, "a", "vision", json!("caption"));
        assert_eq!(meta[0].get("vision"), Some(&json!("caption")));
        assert!(meta[1].get("vision").is_none());
        // Unknown id is a no-op.
        set_meta(&mut meta, "zzz", "vision", json!("x"));
        assert!(meta[1].get("vision").is_none());
    }

    #[test]
    fn truthy_matches_python() {
        assert!(truthy(&json!(true)));
        assert!(!truthy(&json!(false)));
        assert!(!truthy(&Value::Null));
        assert!(!truthy(&json!("")));
        assert!(truthy(&json!("x")));
        assert!(!truthy(&json!(0)));
        assert!(truthy(&json!(1)));
    }

    // --- vision-model detection (chat_helpers parity) ---

    #[test]
    fn vl_name_match_standalone_token() {
        // `(?<![a-z])vl(?![a-z])` — flanked by non-letters (or string edges).
        assert!(vl_name_match("qwen2.5-vl"));
        assert!(vl_name_match("qwen2.5-vl-7b"));
        assert!(vl_name_match("vl"));
        assert!(vl_name_match("model-vl2")); // "vl" preceded by '-', followed by a digit.
        // `vlm` literal (matches even when flanked by letters).
        assert!(vl_name_match("some-vlm-model"));
        // "vl" preceded by a letter (internvl) is NOT a standalone token — it is
        // caught by the `internvl` keyword in is_vision_model, not by vl_name_match.
        assert!(!vl_name_match("internvl2"));
        // NOT a standalone token: "vl" flanked by lowercase letters on both sides.
        assert!(!vl_name_match("travldo")); // "vl" flanked by letters a/d.
        assert!(!vl_name_match("gpt-4o")); // no "vl"/"vlm" at all.
    }

    #[test]
    fn is_vision_model_keywords_and_vl() {
        // Expanded keyword list.
        assert!(is_vision_model("gpt-4o"));
        assert!(is_vision_model("claude-opus-4"));
        assert!(is_vision_model("gemma3:4b"));
        assert!(is_vision_model("llama-4-scout"));
        assert!(is_vision_model("mistral-small-3.1"));
        assert!(is_vision_model("phi-4-multimodal"));
        assert!(is_vision_model("moondream"));
        assert!(is_vision_model("glm-4.5v"));
        assert!(is_vision_model("InternVL")); // case-insensitive via lowercasing.
        // VL family via the regex fallback.
        assert!(is_vision_model("Qwen2.5-VL-7B"));
        // Plainly text-only.
        assert!(!is_vision_model("llama3:8b"));
        assert!(!is_vision_model("mistral-7b"));
        assert!(!is_vision_model(""));
    }

    #[test]
    fn is_local_host_matches_python() {
        // Named loopback / docker / mDNS.
        assert!(is_local_host("localhost"));
        assert!(is_local_host("host.docker.internal"));
        assert!(is_local_host("mybox.local"));
        // Loopback / RFC1918 / link-local / CGNAT (Tailscale).
        assert!(is_local_host("127.0.0.1"));
        assert!(is_local_host("192.168.1.5"));
        assert!(is_local_host("10.0.0.1"));
        assert!(is_local_host("172.16.0.1"));
        assert!(is_local_host("169.254.0.1"));
        assert!(is_local_host("100.100.5.5")); // 100.64.0.0/10
        // Bare hostname without a dot -> local per Python's `"." not in host`.
        assert!(is_local_host("mybox"));
        // Public.
        assert!(!is_local_host("api.openai.com"));
        assert!(!is_local_host("8.8.8.8"));
        assert!(!is_local_host("100.200.0.1")); // outside 100.64/10
        assert!(!is_local_host(""));
    }

    #[test]
    fn model_supports_vision_falls_back_to_name_when_no_endpoint() {
        // No endpoint -> pure name detection.
        assert!(model_supports_vision("gpt-4o", ""));
        assert!(!model_supports_vision("llama3:8b", ""));
        // A public endpoint is never probed (not local host) -> name fallback.
        assert!(model_supports_vision("qwen2.5-vl", "https://api.example.com/v1"));
        assert!(!model_supports_vision("llama3:8b", "https://api.example.com/v1"));
    }

    #[test]
    fn cgnat_range_boundaries() {
        // 100.64.0.0/10 spans 100.64.x.x .. 100.127.x.x.
        assert!(in_cgnat_100_64("100.64.0.0".parse().unwrap()));
        assert!(in_cgnat_100_64("100.127.255.255".parse().unwrap()));
        assert!(!in_cgnat_100_64("100.63.0.0".parse().unwrap()));
        assert!(!in_cgnat_100_64("100.128.0.0".parse().unwrap()));
    }
}
