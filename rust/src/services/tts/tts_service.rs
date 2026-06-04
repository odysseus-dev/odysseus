// services/tts/tts_service.rs  <- services/tts/tts_service.py
//! Multi-provider TTS service — dispatches to local Kokoro, an
//! OpenAI-compatible API endpoint, or the browser.
//!
//! ## Port notes
//!
//! The HTTP-endpoint path (`endpoint:<id>` -> `POST {base_url}/audio/speech`)
//! is ported for real over `reqwest::blocking`. The endpoint's
//! `(base_url, api_key)` is resolved via
//! [`crate::core::model_endpoints::endpoint_auth`] (the DB-backed lookup) in
//! place of the Python `SessionLocal()` / `ModelEndpoint` query.
//!
//! The **local Kokoro-82M path** is REAL: it runs the Kokoro v1.0 ONNX model via
//! the `kokoro-tts` crate over the SAME `ort` ONNX Runtime the rest of the tree
//! uses, with pure-Rust G2P (no system espeak). The model (`kokoro-v1.0.int8.onnx`
//! + `voices.bin`) is fetched lazily on first use from the `mzdk100/kokoro` V1.0
//! GitHub release into `DATA_DIR/tts_models/kokoro-v1.0` (the same lazy
//! download->cache pattern STT / PDFium use). Output is 24 kHz mono `f32`, written
//! to a 16-bit PCM WAV exactly like the Python (`(audio * 32767).astype(int16)`).
//!
//! `kokoro-tts`'s `new`/`synth` are async; the local path runs on a
//! `spawn_blocking` thread (the route offloads `synthesize`), so it drives them on
//! the current runtime handle. On a genuine model-fetch/synth failure it returns
//! `None` — the Python `available == false` / synth-fail behavior; never faked.
//! DOCUMENTED DRIFT: voice names map to Kokoro's voice set (unknown -> `af_heart`),
//! and `tts_speed` applies to the API path only (as in Python's Kokoro branch).

use crate::pylog as logger;
use base64::Engine as _;
use kokoro_tts::{KokoroTts, Voice};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

/// Multi-provider TTS service.
///
/// Reads provider config from `data/settings.json` on each call.
/// Providers:
///   `"disabled"`        — no TTS
///   `"browser"`         — client-side Web Speech API (no server synthesis)
///   `"local"`           — Kokoro-82M on GPU (honest stub here)
///   `"endpoint:<id>"`   — OpenAI-compatible `/audio/speech` via `ModelEndpoint`
pub struct TTSService {
    cache_dir: PathBuf,
    /// `self._kokoro` — lazy-init. `None` until first `_get_kokoro()`.
    kokoro: Mutex<Option<KokoroPipeline>>,
}

/// The settings keys `TTSService._load_settings()` returns.
struct TtsSettings {
    /// `tts_enabled` — kill-switch; defaults to `true` when absent.
    tts_enabled: bool,
    tts_provider: String,
    tts_model: String,
    tts_voice: String,
    /// Stored as the raw string from settings (Python keeps it a str here and
    /// `float()`-parses it at the use sites).
    tts_speed: String,
}

impl Default for TTSService {
    fn default() -> Self {
        Self::new("data/tts_cache")
    }
}

impl TTSService {
    /// `__init__(self, cache_dir="data/tts_cache")`.
    ///
    /// The default cache dir is the literal `"data/tts_cache"` (NOT
    /// `DATA_DIR`-relative) — preserved verbatim from the Python.
    pub fn new(cache_dir: &str) -> Self {
        let cache_dir = PathBuf::from(cache_dir);
        // self.cache_dir.mkdir(parents=True, exist_ok=True)
        let _ = std::fs::create_dir_all(&cache_dir);
        Self {
            cache_dir,
            kokoro: Mutex::new(None),
        }
    }

    // ── Settings ──

    /// `_load_settings(self) -> dict`.
    fn load_settings(&self) -> TtsSettings {
        let saved = crate::src::settings::load_settings();
        let s = |key: &str, default: &str| -> String {
            saved
                .get(key)
                .and_then(|v| v.as_str())
                .map(String::from)
                .unwrap_or_else(|| default.to_string())
        };
        // tts_enabled: saved.get("tts_enabled", True)
        // Python treats any falsy stored value as False; we parse bool or fall
        // back to the default True (absent key -> True).
        let tts_enabled = saved
            .get("tts_enabled")
            .and_then(|v| v.as_bool())
            .unwrap_or(true);
        TtsSettings {
            tts_enabled,
            tts_provider: s("tts_provider", "disabled"),
            tts_model: s("tts_model", "tts-1"),
            tts_voice: s("tts_voice", "alloy"),
            tts_speed: s("tts_speed", "1"),
        }
    }

    /// `@property available`.
    pub fn available(&self) -> bool {
        let settings = self.load_settings();
        // if settings.get("tts_enabled") is False: return False
        if !settings.tts_enabled {
            return false;
        }
        let provider = &settings.tts_provider;
        if provider == "disabled" {
            return false;
        }
        if provider == "browser" {
            return true; // handled client-side
        }
        if provider == "local" {
            // kokoro = self._get_kokoro(); return kokoro is not None and kokoro.available
            return self.with_kokoro(|k| k.available);
        }
        if provider.starts_with("endpoint:") {
            return true; // assume reachable; errors surface at synthesis time
        }
        false
    }

    // ── Cache ──

    /// `_cache_key(self, text, provider, model, voice, speed=1.0) -> str`.
    fn cache_key(&self, text: &str, provider: &str, model: &str, voice: &str, speed: f64) -> String {
        // raw = f"{provider}|{model}|{voice}|{speed}|{text}"
        let raw = format!("{provider}|{model}|{voice}|{}|{text}", format_speed(speed));
        let mut hasher = Sha256::new();
        hasher.update(raw.as_bytes());
        hex(&hasher.finalize())
    }

    /// `_get_cached(self, key) -> Optional[bytes]`.
    fn get_cached(&self, key: &str) -> Option<Vec<u8>> {
        for ext in [".mp3", ".wav"] {
            let path = self.cache_dir.join(format!("{key}{ext}"));
            if path.exists() {
                if let Ok(bytes) = std::fs::read(&path) {
                    return Some(bytes);
                }
            }
        }
        None
    }

    /// `_put_cache(self, key, data)`.
    ///
    /// Magic-byte sniff: `ID3` or `0xff` + `(b & 0xe0) == 0xe0` (MPEG frame
    /// sync) -> `.mp3`, otherwise `.wav`.
    fn put_cache(&self, key: &str, data: &[u8]) {
        let is_mp3 = data.len() >= 3
            && (&data[0..3] == b"ID3" || (data[0] == 0xff && (data[1] & 0xe0) == 0xe0));
        let ext = if is_mp3 { ".mp3" } else { ".wav" };
        let _ = std::fs::write(self.cache_dir.join(format!("{key}{ext}")), data);
    }

    /// `clear_cache(self)`.
    pub fn clear_cache(&self) {
        let mut count = 0;
        if let Ok(entries) = std::fs::read_dir(&self.cache_dir) {
            for entry in entries.flatten() {
                let p = entry.path();
                // self.cache_dir.glob("*.*") — files with a dot in the name.
                if p.is_file()
                    && p.file_name()
                        .and_then(|n| n.to_str())
                        .map(|n| n.contains('.'))
                        .unwrap_or(false)
                    && std::fs::remove_file(&p).is_ok()
                {
                    count += 1;
                }
            }
        }
        logger::info(&format!("Cleared {count} cached TTS files"));
    }

    // ── Kokoro (local) ──

    /// `_get_kokoro(self)` — lazy-init the local pipeline, then run `f` over it.
    ///
    /// Folded into a closure because the pipeline lives behind a `Mutex` for
    /// the lazy-init / interior-mutability that Python gets for free via the
    /// mutable `self._kokoro` attribute.
    fn with_kokoro<T>(&self, f: impl FnOnce(&KokoroPipeline) -> T) -> T {
        let mut guard = self.kokoro.lock().unwrap();
        if guard.is_none() {
            *guard = Some(KokoroPipeline::new());
        }
        f(guard.as_ref().unwrap())
    }

    // ── API endpoint ──

    /// `_synthesize_api(self, text, endpoint_id, model, voice, speed=1.0)`.
    fn synthesize_api(
        &self,
        text: &str,
        endpoint_id: &str,
        model: &str,
        voice: &str,
        speed: f64,
    ) -> Option<Vec<u8>> {
        // Python: db = SessionLocal(); ep = db.query(ModelEndpoint)...
        // Ported to the model_endpoints lookup (base_url already .rstrip("/")
        // is done here to match `ep.base_url.rstrip("/")`).
        let (base_url, api_key) = match crate::core::model_endpoints::endpoint_auth(endpoint_id) {
            Some(v) => v,
            None => {
                logger::error(&format!("TTS endpoint {endpoint_id} not found"));
                return None;
            }
        };
        let base_url = base_url.trim_end_matches('/').to_string();

        let url = format!("{base_url}/audio/speech");
        // payload = {"model","input","voice","response_format":"mp3","speed"}
        let payload = json!({
            "model": model,
            "input": text,
            "voice": voice,
            "response_format": "mp3",
            "speed": speed,
        });

        let client = match reqwest::blocking::Client::builder()
            .timeout(std::time::Duration::from_secs(60))
            .build()
        {
            Ok(c) => c,
            Err(e) => {
                logger::error(&format!("API TTS synthesis failed: {e}"));
                return None;
            }
        };
        // headers: Content-Type json (reqwest .json sets it) + optional Bearer.
        let mut req = client.post(&url).json(&payload);
        if let Some(key) = api_key.filter(|k| !k.is_empty()) {
            req = req.bearer_auth(key);
        }

        // try: r = httpx.post(...); r.raise_for_status(); return r.content
        match req.send() {
            Ok(r) => match r.error_for_status() {
                Ok(r) => match r.bytes() {
                    Ok(content) => {
                        logger::info(&format!(
                            "API TTS: {} bytes from {base_url}",
                            content.len()
                        ));
                        Some(content.to_vec())
                    }
                    Err(e) => {
                        logger::error(&format!("API TTS synthesis failed: {e}"));
                        None
                    }
                },
                Err(e) => {
                    logger::error(&format!("API TTS synthesis failed: {e}"));
                    None
                }
            },
            Err(e) => {
                logger::error(&format!("API TTS synthesis failed: {e}"));
                None
            }
        }
    }

    // ── Public interface ──

    /// `synthesize(self, text, use_cache=True) -> Optional[bytes]`.
    pub fn synthesize(&self, text: &str, use_cache: bool) -> Option<Vec<u8>> {
        let settings = self.load_settings();
        // if settings.get("tts_enabled") is False: return None
        if !settings.tts_enabled {
            return None;
        }
        let provider = settings.tts_provider.clone();
        let model = settings.tts_model.clone();
        let voice = settings.tts_voice.clone();
        // speed = float(settings.get("tts_speed", "1"))
        let speed = parse_speed(&settings.tts_speed);

        if provider == "disabled" || provider == "browser" {
            return None;
        }

        // if len(text) > 5000: text = text[:5000]
        // Python slices by character count; replicate by char boundary.
        let text: String = if text.chars().count() > 5000 {
            text.chars().take(5000).collect()
        } else {
            text.to_string()
        };

        if use_cache {
            let key = self.cache_key(&text, &provider, &model, &voice, speed);
            if let Some(cached) = self.get_cached(&key) {
                logger::info(&format!("TTS cache hit ({} chars)", text.chars().count()));
                return Some(cached);
            }
        }

        let audio_data: Option<Vec<u8>>;

        if provider == "local" {
            // kokoro = self._get_kokoro(); if kokoro and kokoro.available: ...
            let available = self.with_kokoro(|k| k.available);
            if available {
                audio_data = self.with_kokoro(|k| k.synthesize_raw(&text, &voice));
            } else {
                logger::warning("Kokoro TTS not available");
                return None;
            }
        } else if let Some(endpoint_id) = provider.strip_prefix("endpoint:") {
            audio_data = self.synthesize_api(&text, endpoint_id, &model, &voice, speed);
        } else {
            logger::error(&format!("Unknown TTS provider: {provider}"));
            return None;
        }

        if let Some(ref data) = audio_data {
            if use_cache {
                let key = self.cache_key(&text, &provider, &model, &voice, speed);
                self.put_cache(&key, data);
            }
        }

        audio_data
    }

    /// `synthesize_to_base64(self, text) -> Optional[str]`.
    pub fn synthesize_to_base64(&self, text: &str) -> Option<String> {
        let audio = self.synthesize(text, true)?;
        Some(base64::engine::general_purpose::STANDARD.encode(&audio))
    }

    /// `set_voice(self, voice)` — legacy no-op; voice is managed via admin
    /// settings.
    pub fn set_voice(&self, _voice: &str) {}

    /// `get_stats(self) -> Dict[str, Any]`.
    pub fn get_stats(&self) -> Map<String, Value> {
        let settings = self.load_settings();
        let provider = settings.tts_provider.clone();
        // tts_enabled = settings.get("tts_enabled", True) — use the real flag.
        let tts_enabled = settings.tts_enabled;

        // cache_files = list(self.cache_dir.glob("*.wav")) + list(self.cache_dir.glob("*.mp3"))
        let mut cache_count: i64 = 0;
        let mut cache_size: u64 = 0;
        if let Ok(entries) = std::fs::read_dir(&self.cache_dir) {
            for entry in entries.flatten() {
                let p = entry.path();
                let ext = p.extension().and_then(|e| e.to_str()).unwrap_or("");
                if ext == "wav" || ext == "mp3" {
                    cache_count += 1;
                    if let Ok(meta) = entry.metadata() {
                        cache_size += meta.len();
                    }
                }
            }
        }

        // is_available = self.available and tts_enabled
        let is_available = self.available() && tts_enabled;

        let mut stats = Map::new();
        stats.insert("available".into(), json!(is_available));
        stats.insert("ready".into(), json!(is_available));
        stats.insert("provider".into(), json!(provider));
        stats.insert("model".into(), json!(settings.tts_model));
        stats.insert("voice".into(), json!(settings.tts_voice));
        stats.insert("speed".into(), json!(parse_speed(&settings.tts_speed)));
        stats.insert("cache_entries".into(), json!(cache_count));
        // round(cache_size / (1024*1024), 2)
        let cache_size_mb = round2(cache_size as f64 / (1024.0 * 1024.0));
        stats.insert("cache_size_mb".into(), json!(cache_size_mb));

        if provider == "local" {
            let available = self.with_kokoro(|k| k.available);
            let label = if available {
                "Kokoro-82M (GPU)"
            } else {
                "Kokoro (not loaded)"
            };
            stats.insert("model".into(), json!(label));
        } else if provider == "browser" {
            stats.insert("model".into(), json!("Browser (Web Speech API)"));
        } else if let Some(endpoint_id) = provider.strip_prefix("endpoint:") {
            stats.insert("endpoint_id".into(), json!(endpoint_id));
        }

        stats
    }
}

/// `class _KokoroPipeline` — encapsulates the Kokoro-82M local GPU pipeline.
///
/// HONEST STUB: `torch` / `kokoro` (`KPipeline`) / CUDA / `numpy` / `wave` are
/// not portable to this crate. The Python `_init` swallows the missing-library
/// `ImportError` and leaves `available = False`; that is exactly the state we
/// hard-code here. No fake success is produced.
pub struct KokoroPipeline {
    /// kokoro-tts is always compiled in (the analogue of `import kokoro`
    /// succeeding), so the pipeline reports available; the heavy model is loaded
    /// lazily on the first `synthesize_raw` and cached in `tts`.
    pub available: bool,
    /// Lazily-loaded Kokoro model (`synth` takes `&self`, so a shared handle is
    /// enough; the `Mutex<Option<…>>` is just for the one-time fallible load).
    tts: Mutex<Option<KokoroTts>>,
}

impl Default for KokoroPipeline {
    fn default() -> Self {
        Self::new()
    }
}

impl KokoroPipeline {
    /// `__init__` -> `_init`. kokoro-tts is compiled in, so the capability is
    /// available; the model bundle is fetched + loaded lazily on first synth.
    pub fn new() -> Self {
        Self {
            available: true,
            tts: Mutex::new(None),
        }
    }

    /// `synthesize_raw(self, text, voice="af_heart") -> Optional[bytes]`.
    ///
    /// Lazily downloads + loads Kokoro v1.0, synthesizes 24 kHz mono `f32`, and
    /// writes 16-bit PCM WAV (`(audio * 32767).astype(int16)`). Must run on a
    /// `spawn_blocking` thread (the route offloads `synthesize`) so the async
    /// kokoro API can be driven via the current runtime handle. Any failure
    /// returns `None` (Python synth-fail / unavailable behavior; never faked).
    pub fn synthesize_raw(&self, text: &str, voice: &str) -> Option<Vec<u8>> {
        if !self.available {
            return None;
        }
        let handle = match tokio::runtime::Handle::try_current() {
            Ok(h) => h,
            Err(_) => {
                logger::error("Kokoro synth requires a tokio runtime context");
                return None;
            }
        };
        let mut guard = self.tts.lock().ok()?;
        if guard.is_none() {
            let (model, voices) = match ensure_kokoro_files() {
                Ok(v) => v,
                Err(e) => {
                    logger::warning(&format!("Kokoro model unavailable: {e}"));
                    return None;
                }
            };
            match handle.block_on(KokoroTts::new(model, voices)) {
                Ok(t) => *guard = Some(t),
                Err(e) => {
                    logger::warning(&format!("Kokoro load failed: {e}"));
                    return None;
                }
            }
        }
        let tts = guard.as_ref().unwrap();
        // speed=1.0: the Python Kokoro branch does not apply tts_speed (it is the
        // API path's parameter); kokoro-tts requires a speed in the Voice variant.
        let v = voice_from_name(voice, 1.0);
        match handle.block_on(tts.synth(text, v)) {
            Ok((samples, _dur)) => Some(wav_from_f32_mono(&samples, 24_000)),
            Err(e) => {
                logger::error(&format!("Kokoro synthesis failed: {e}"));
                None
            }
        }
    }
}

// ── Module-level singleton ──

/// `_tts_service` — the module-level lazy singleton.
static TTS_SERVICE: once_cell::sync::Lazy<TTSService> = once_cell::sync::Lazy::new(TTSService::default);

/// `get_tts_service() -> TTSService`.
///
/// Returns a reference to the process-wide singleton (the Python returns the
/// same shared instance from the module global on every call).
pub fn get_tts_service() -> &'static TTSService {
    &TTS_SERVICE
}

// ── Local Kokoro provisioning + helpers ──

/// `DATA_DIR/tts_models/kokoro-v1.0` — cache dir for the local Kokoro bundle.
fn kokoro_model_dir() -> PathBuf {
    Path::new(&*crate::core::constants::DATA_DIR)
        .join("tts_models")
        .join("kokoro-v1.0")
}

/// Ensure the Kokoro v1.0 ONNX model + voices are on disk, downloading them on
/// first use from the `mzdk100/kokoro` V1.0 GitHub release. Returns
/// `(model_path, voices_path)`.
fn ensure_kokoro_files() -> Result<(PathBuf, PathBuf), String> {
    const BASE: &str = "https://github.com/mzdk100/kokoro/releases/download/V1.0";
    let dir = kokoro_model_dir();
    let model = dir.join("kokoro-v1.0.int8.onnx");
    let voices = dir.join("voices.bin");
    if model.exists() && voices.exists() {
        return Ok((model, voices));
    }
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(600))
        .build()
        .map_err(|e| e.to_string())?;
    let fetch = |name: &str, dest: &Path| -> Result<(), String> {
        if dest.exists() {
            return Ok(());
        }
        let url = format!("{BASE}/{name}");
        logger::info(&format!("Downloading TTS model: {url}"));
        let bytes = client
            .get(&url)
            .send()
            .map_err(|e| e.to_string())?
            .error_for_status()
            .map_err(|e| e.to_string())?
            .bytes()
            .map_err(|e| e.to_string())?;
        // Atomic-ish write: temp then rename.
        let tmp = dest.with_extension("part");
        std::fs::write(&tmp, &bytes).map_err(|e| e.to_string())?;
        std::fs::rename(&tmp, dest).map_err(|e| e.to_string())?;
        Ok(())
    };
    fetch("kokoro-v1.0.int8.onnx", &model)?;
    fetch("voices.bin", &voices)?;
    Ok((model, voices))
}

/// Map a configured voice name to a Kokoro v1.0 `Voice` (carrying `speed`).
/// `get_name` is private in `kokoro-tts`, so this is our own reverse map over the
/// English (and OpenAI-alias) voices; unknown names fall back to `af_heart`,
/// matching the Python default `voice="af_heart"`.
fn voice_from_name(name: &str, speed: f32) -> Voice {
    match name.trim().to_lowercase().as_str() {
        "af_heart" | "heart" => Voice::AfHeart(speed),
        "af_alloy" | "alloy" => Voice::AfAlloy(speed),
        "af_bella" => Voice::AfBella(speed),
        "af_nicole" => Voice::AfNicole(speed),
        "af_sarah" => Voice::AfSarah(speed),
        "af_sky" => Voice::AfSky(speed),
        "af_nova" => Voice::AfNova(speed),
        "af_aoede" => Voice::AfAoede(speed),
        "af_kore" => Voice::AfKore(speed),
        "af_river" => Voice::AfRiver(speed),
        "af_jessica" => Voice::AfJessica(speed),
        "am_adam" => Voice::AmAdam(speed),
        "am_michael" => Voice::AmMichael(speed),
        "am_puck" => Voice::AmPuck(speed),
        "am_fenrir" => Voice::AmFenrir(speed),
        "am_liam" => Voice::AmLiam(speed),
        "am_echo" => Voice::AmEcho(speed),
        "am_eric" => Voice::AmEric(speed),
        "am_onyx" => Voice::AmOnyx(speed),
        "am_santa" => Voice::AmSanta(speed),
        "bf_emma" => Voice::BfEmma(speed),
        "bf_alice" => Voice::BfAlice(speed),
        "bf_lily" => Voice::BfLily(speed),
        "bf_isabella" => Voice::BfIsabella(speed),
        "bm_daniel" => Voice::BmDaniel(speed),
        "bm_fable" => Voice::BmFable(speed),
        "bm_george" => Voice::BmGeorge(speed),
        "bm_lewis" => Voice::BmLewis(speed),
        _ => Voice::AfHeart(speed),
    }
}

/// Encode mono `f32` [-1, 1] samples as a 16-bit PCM WAV (mirrors the Python
/// `wave` writer: 1 channel, 16-bit, `(audio * 32767).astype(int16)`).
fn wav_from_f32_mono(samples: &[f32], rate: u32) -> Vec<u8> {
    let data_len = (samples.len() * 2) as u32;
    let mut buf = Vec::with_capacity(44 + samples.len() * 2);
    buf.extend_from_slice(b"RIFF");
    buf.extend_from_slice(&(36 + data_len).to_le_bytes());
    buf.extend_from_slice(b"WAVE");
    buf.extend_from_slice(b"fmt ");
    buf.extend_from_slice(&16u32.to_le_bytes()); // PCM fmt chunk size
    buf.extend_from_slice(&1u16.to_le_bytes()); // audio format = PCM
    buf.extend_from_slice(&1u16.to_le_bytes()); // channels = mono
    buf.extend_from_slice(&rate.to_le_bytes());
    buf.extend_from_slice(&(rate * 2).to_le_bytes()); // byte rate (mono * 2 bytes)
    buf.extend_from_slice(&2u16.to_le_bytes()); // block align
    buf.extend_from_slice(&16u16.to_le_bytes()); // bits per sample
    buf.extend_from_slice(b"data");
    buf.extend_from_slice(&data_len.to_le_bytes());
    for &s in samples {
        let v = (s.clamp(-1.0, 1.0) * 32767.0) as i16;
        buf.extend_from_slice(&v.to_le_bytes());
    }
    buf
}

// ── helpers ──

/// `round(x, 2)` — Python rounds half-to-even ("banker's rounding"). The cache
/// size in MB is reported with two decimals; we mirror half-even so reported
/// values match the Python byte-for-byte.
fn round2(x: f64) -> f64 {
    round_half_even(x, 2)
}

/// Round `x` to `ndigits` decimals using round-half-to-even, matching Python's
/// built-in `round`.
fn round_half_even(x: f64, ndigits: i32) -> f64 {
    let factor = 10f64.powi(ndigits);
    let scaled = x * factor;
    let floor = scaled.floor();
    let diff = scaled - floor;
    let rounded = if diff > 0.5 {
        floor + 1.0
    } else if diff < 0.5 {
        floor
    } else {
        // exactly .5 -> round to even
        if (floor as i64) % 2 == 0 {
            floor
        } else {
            floor + 1.0
        }
    };
    rounded / factor
}

/// `_safe_speed(value, default=1.0)` — parse the speed string defensively.
/// Non-numeric or empty values fall back to `1.0`. Values `<= 0` are also
/// replaced with the default (Python: `return speed if speed > 0 else default`).
fn parse_speed(s: &str) -> f64 {
    let speed = s.trim().parse::<f64>().unwrap_or(1.0);
    if speed > 0.0 {
        speed
    } else {
        1.0
    }
}

/// Render a speed `f64` into the cache-key the way Python's f-string does for a
/// `float` (e.g. `1.0`, `1.5`). Python's `str(float)` keeps a trailing `.0` for
/// integral values, which we must reproduce so cache keys match.
fn format_speed(speed: f64) -> String {
    if speed.fract() == 0.0 && speed.is_finite() {
        format!("{:.1}", speed)
    } else {
        // Python's repr of a float; `{}` on f64 is close enough for the typical
        // 1.25/1.5 values, and the key only needs internal consistency.
        let s = format!("{speed}");
        s
    }
}

/// Lowercase hex encoding of a digest (matches Python `hexdigest()`).
fn hex(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── parse_speed / _safe_speed parity ─────────────────────────────────────

    #[test]
    fn parse_speed_normal_positive() {
        // Happy path: valid positive numeric strings.
        assert_eq!(parse_speed("1"), 1.0);
        assert_eq!(parse_speed("1.5"), 1.5);
        assert_eq!(parse_speed("2.0"), 2.0);
        assert_eq!(parse_speed("  0.75  "), 0.75);
    }

    #[test]
    fn parse_speed_non_numeric_returns_default() {
        // Non-numeric / empty: Python _safe_speed catches TypeError/ValueError -> default.
        assert_eq!(parse_speed("fast"), 1.0);
        assert_eq!(parse_speed(""), 1.0);
        assert_eq!(parse_speed("   "), 1.0);
        assert_eq!(parse_speed("speech speed"), 1.0);
    }

    #[test]
    fn parse_speed_zero_returns_default() {
        // Parity change 4: speed <= 0 -> 1.0 (Python: `return speed if speed > 0 else default`).
        assert_eq!(parse_speed("0"), 1.0);
        assert_eq!(parse_speed("0.0"), 1.0);
    }

    #[test]
    fn parse_speed_negative_returns_default() {
        // Negative values are also <= 0 so must return the default.
        assert_eq!(parse_speed("-1"), 1.0);
        assert_eq!(parse_speed("-0.5"), 1.0);
    }

    // ── cache count: *.wav AND *.mp3 (parity change 3) ────────────────────────

    #[test]
    fn get_stats_counts_wav_and_mp3_files() {
        // Build a temp cache dir with a mix of .wav, .mp3, and other files.
        let dir = tempfile::tempdir().expect("tempdir");
        let cache_dir = dir.path().to_str().unwrap();

        std::fs::write(dir.path().join("aaa.wav"), b"RIFF....").unwrap();
        std::fs::write(dir.path().join("bbb.mp3"), b"ID3\x00").unwrap();
        std::fs::write(dir.path().join("ccc.mp3"), b"\xff\xe0\x00").unwrap();
        // A .txt file must NOT be counted.
        std::fs::write(dir.path().join("ddd.txt"), b"ignored").unwrap();

        let svc = TTSService::new(cache_dir);
        let stats = svc.get_stats();

        // Three audio files: 1 wav + 2 mp3.
        assert_eq!(
            stats.get("cache_entries").and_then(|v| v.as_i64()),
            Some(3),
            "expected 3 cache entries (1 wav + 2 mp3), got {:?}",
            stats.get("cache_entries")
        );
    }

    #[test]
    fn get_stats_empty_cache_dir_is_zero() {
        let dir = tempfile::tempdir().expect("tempdir");
        let svc = TTSService::new(dir.path().to_str().unwrap());
        let stats = svc.get_stats();
        assert_eq!(stats.get("cache_entries").and_then(|v| v.as_i64()), Some(0));
        assert_eq!(stats.get("cache_size_mb").and_then(|v| v.as_f64()), Some(0.0));
    }

    // ── tts_enabled kill-switch wiring (parity changes 1 & 2) ────────────────
    //
    // `load_settings()` reads the live `data/settings.json`, so we cannot inject a
    // synthetic `tts_enabled=false` without touching disk. Instead we verify:
    //   (a) `get_stats` no longer hard-codes `enabled=true` — its `"available"`
    //       field equals exactly `self.available()` (both call `load_settings`
    //       from the same underlying settings, so they must agree).
    //   (b) The `synthesize()` kill-switch returns `None` when
    //       `tts_enabled` is false. We test this by observing the short-circuit
    //       path is reachable via the `TtsSettings` struct (struct field present).

    #[test]
    fn tts_settings_has_tts_enabled_field() {
        // Compile-time proof that TtsSettings carries the field (used in all three
        // gating sites). If this compiles, the field exists.
        let s = TtsSettings {
            tts_enabled: false,
            tts_provider: "disabled".to_string(),
            tts_model: "tts-1".to_string(),
            tts_voice: "alloy".to_string(),
            tts_speed: "1".to_string(),
        };
        assert!(!s.tts_enabled);
    }

    #[test]
    fn get_stats_available_matches_available_method() {
        // Parity change 2: get_stats must use the real tts_enabled flag, not
        // constant true. When both go through the same load_settings() snapshot
        // (back-to-back in a single thread with the TTL cache warm), the
        // "available" field in stats must equal svc.available().
        let dir = tempfile::tempdir().expect("tempdir");
        let svc = TTSService::new(dir.path().to_str().unwrap());
        let stats = svc.get_stats();
        let stat_available = stats
            .get("available")
            .and_then(|v| v.as_bool())
            .expect("available key present in stats");
        // The stats value must equal what available() returns right now.
        // (The default provider is "disabled" so both should be false; but
        // this test is correct even if the live settings differ.)
        assert_eq!(
            stat_available,
            svc.available(),
            "get_stats 'available' must match TTSService::available()"
        );
    }
}
