# services/tts/tts_service.py
"""Multi-provider TTS service — local Kokoro (GPU/CPU), local Piper (CPU),
OpenAI-compatible API endpoints, or client-side browser Web Speech."""

import io
import json
import time
import wave
import logging
import hashlib
import threading
import httpx
from pathlib import Path
from typing import Optional, Dict, Any, List

from src.constants import TTS_CACHE_DIR, PIPER_VOICES_DIR

logger = logging.getLogger(__name__)

# Official Piper voice repository (Hugging Face). voices.json lists every
# voice with its file paths, sizes, and language metadata.
PIPER_HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"
PIPER_CATALOG_URL = f"{PIPER_HF_BASE}/voices.json"
_CATALOG_TTL_SECONDS = 24 * 3600

# Known Kokoro-82M voice ids. The kokoro package fetches the voice tensors
# from Hugging Face automatically on first use, so unlike Piper there is no
# download management — this static list just feeds the settings dropdown.
KOKORO_VOICES = [
    {"id": v, "name": v, "language": lang}
    for v, lang in [
        ("af_heart", "American English"), ("af_alloy", "American English"),
        ("af_aoede", "American English"), ("af_bella", "American English"),
        ("af_jessica", "American English"), ("af_kore", "American English"),
        ("af_nicole", "American English"), ("af_nova", "American English"),
        ("af_river", "American English"), ("af_sarah", "American English"),
        ("af_sky", "American English"),
        ("am_adam", "American English"), ("am_echo", "American English"),
        ("am_eric", "American English"), ("am_fenrir", "American English"),
        ("am_liam", "American English"), ("am_michael", "American English"),
        ("am_onyx", "American English"), ("am_puck", "American English"),
        ("bf_alice", "British English"), ("bf_emma", "British English"),
        ("bf_isabella", "British English"), ("bf_lily", "British English"),
        ("bm_daniel", "British English"), ("bm_fable", "British English"),
        ("bm_george", "British English"), ("bm_lewis", "British English"),
    ]
]


def _safe_speed(value, default: float = 1.0) -> float:
    """Parse the stored tts_speed defensively. The settings layer tolerates
    corrupt/agent-written config, so a non-numeric or empty value (e.g. an agent
    setting "speech speed" = "fast", or a hand-edited settings.json) must not
    crash synthesis or the stats endpoint with a ValueError."""
    try:
        speed = float(value)
    except (TypeError, ValueError):
        return default
    return speed if speed > 0 else default


class TTSService:
    """Multi-provider TTS service.

    Reads provider config from data/settings.json on each call.
    Providers:
      "disabled"        — no TTS
      "browser"         — client-side Web Speech API (no server synthesis)
      "local"           — Kokoro-82M on GPU
      "piper"           — Piper ONNX voices on CPU
      "endpoint:<id>"   — OpenAI-compatible /audio/speech via ModelEndpoint
    """

    def __init__(self, cache_dir: str = TTS_CACHE_DIR, piper_voices_dir: str = PIPER_VOICES_DIR):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.piper_voices_dir = Path(piper_voices_dir)
        self._kokoro = None  # lazy-init
        self._piper = None   # lazy-init

    # ── Settings ──

    def _load_settings(self, owner: str = "") -> dict:
        from src.settings import load_settings, get_user_setting
        saved = load_settings()

        # Voice is a per-user preference; everything else stays admin-global.
        voice = saved.get("tts_voice", "alloy")
        if owner:
            voice = get_user_setting("tts_voice", owner, voice)
        else:
            # Auth disabled — get_user_setting needs an owner, but the prefs
            # API still writes into the first user slot, so read it directly.
            try:
                from routes.prefs_routes import _load_for_user
                pref = (_load_for_user(None) or {}).get("tts_voice")
                if pref:
                    voice = pref
            except Exception:
                pass

        return {
            "tts_enabled": saved.get("tts_enabled", True),
            "tts_provider": saved.get("tts_provider", "disabled"),
            "tts_model": saved.get("tts_model", "tts-1"),
            "tts_voice": voice,
            "tts_speed": saved.get("tts_speed", "1"),
            "tts_piper_default_voice": saved.get("tts_piper_default_voice", "en_US-lessac-low"),
        }

    def effective_provider(self, settings: Optional[dict] = None) -> "tuple[str, str]":
        """Resolve the provider actually used for synthesis.

        Local providers that aren't usable on this machine (missing package,
        no voice files yet, no CUDA GPU) degrade to client-side browser TTS
        instead of silently turning the feature off for everyone.
        Returns (provider, fallback_reason); fallback_reason is "" when the
        configured provider is used as-is.
        """
        settings = settings or self._load_settings()
        if settings.get("tts_enabled") is False:
            return "disabled", ""
        provider = settings["tts_provider"]
        if provider == "piper":
            piper = self._get_piper()
            if not (piper and piper.available):
                return "browser", "piper-tts is not installed — using browser voices"
            if not piper.list_voices():
                return "browser", "No Piper voices installed yet — using browser voices"
            return "piper", ""
        if provider == "local":
            kokoro = self._get_kokoro()
            if not (kokoro and kokoro.available):
                return "browser", "Kokoro is not installed (pip install kokoro torch soundfile) — using browser voices"
            return "local", ""
        # disabled / browser / endpoint:<id> pass through; endpoint errors
        # surface at synthesis time as before.
        return provider, ""

    @property
    def available(self) -> bool:
        provider, _ = self.effective_provider()
        return provider != "disabled"

    # ── Cache ──

    def _cache_key(self, text: str, provider: str, model: str, voice: str, speed: float = 1.0) -> str:
        raw = f"{provider}|{model}|{voice}|{speed}|{text}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _get_cached(self, key: str) -> Optional[bytes]:
        for ext in (".mp3", ".wav"):
            path = self.cache_dir / f"{key}{ext}"
            if path.exists():
                return path.read_bytes()
        return None

    def _put_cache(self, key: str, data: bytes):
        ext = ".mp3" if (len(data) >= 3 and (data[:3] == b'ID3' or (data[0] == 0xff and (data[1] & 0xe0) == 0xe0))) else ".wav"
        (self.cache_dir / f"{key}{ext}").write_bytes(data)

    def clear_cache(self):
        count = 0
        for f in self.cache_dir.glob("*.*"):
            f.unlink()
            count += 1
        logger.info(f"Cleared {count} cached TTS files")

    # ── Kokoro (local) ──

    def _get_kokoro(self):
        if self._kokoro is None:
            self._kokoro = _KokoroPipeline()
        return self._kokoro

    # ── Piper (local CPU) ──

    def _get_piper(self):
        if self._piper is None:
            self._piper = _PiperPipeline(self.piper_voices_dir)
        return self._piper

    def list_voices(self) -> List[Dict[str, str]]:
        """List installed Piper voices (empty for other providers)."""
        piper = self._get_piper()
        return piper.list_voices() if piper else []

    def _resolve_piper_voice(self, settings: dict) -> Optional[str]:
        """Pick a usable Piper voice id: user pref → global default → first installed."""
        piper = self._get_piper()
        installed = {v["id"] for v in piper.list_voices()}
        if not installed:
            return None
        for candidate in (settings["tts_voice"], settings["tts_piper_default_voice"]):
            if candidate in installed:
                return candidate
        return sorted(installed)[0]

    @staticmethod
    def _resolve_kokoro_voice(settings: dict) -> str:
        """Pick a usable Kokoro voice id. The stored voice may belong to another
        provider (e.g. "alloy" or a Piper id after switching providers) — an
        unknown id would make the kokoro package raise mid-synthesis."""
        voice = settings["tts_voice"]
        if any(v["id"] == voice for v in KOKORO_VOICES):
            return voice
        return "af_heart"

    # ── Piper voice catalog + downloads ──

    def _catalog_cache_path(self) -> Path:
        return self.piper_voices_dir / "_catalog.json"

    def _load_raw_catalog(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Raw voices.json from the Piper voice repo, cached on disk for 24h."""
        cache = self._catalog_cache_path()
        if not force_refresh and cache.exists():
            age = time.time() - cache.stat().st_mtime
            if age < _CATALOG_TTL_SECONDS:
                try:
                    return json.loads(cache.read_text(encoding="utf-8"))
                except Exception:
                    pass  # corrupt cache — refetch below

        r = httpx.get(PIPER_CATALOG_URL, timeout=30, follow_redirects=True)
        r.raise_for_status()
        raw = r.json()
        self.piper_voices_dir.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(raw), encoding="utf-8")
        return raw

    def get_piper_catalog(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Downloadable Piper voices for the settings UI."""
        raw = self._load_raw_catalog(force_refresh)
        installed = {v["id"] for v in self.list_voices()}
        rows = []
        for voice_id, entry in raw.items():
            files = entry.get("files", {}) or {}
            size = sum(
                (meta or {}).get("size_bytes", 0)
                for path, meta in files.items()
                if path.endswith(".onnx") or path.endswith(".onnx.json")
            )
            lang = entry.get("language") or {}
            rows.append({
                "id": voice_id,
                "language": lang.get("name_english") or lang.get("code") or "",
                "language_code": lang.get("code") or "",
                "quality": entry.get("quality") or "",
                "size_mb": round(size / (1024 * 1024), 1),
                "installed": voice_id in installed,
            })
        rows.sort(key=lambda v: (v["language"], v["id"]))
        return rows

    def download_piper_voice(self, voice_id: str) -> None:
        """Download a voice's .onnx + .onnx.json pair from the official repo.
        Blocking — callers run this in a thread. Raises on failure."""
        raw = self._load_raw_catalog()
        entry = raw.get(voice_id)
        if not entry:
            raise ValueError(f"Unknown Piper voice: {voice_id}")

        wanted = [
            path for path in (entry.get("files") or {})
            if path.endswith(".onnx") or path.endswith(".onnx.json")
        ]
        if not wanted:
            raise RuntimeError(f"Catalog entry for {voice_id} lists no model files")

        self.piper_voices_dir.mkdir(parents=True, exist_ok=True)
        for path in wanted:
            dest = self.piper_voices_dir / Path(path).name
            if dest.exists():
                continue
            url = f"{PIPER_HF_BASE}/{path}"
            tmp = dest.with_suffix(dest.suffix + ".part")
            logger.info(f"Downloading Piper voice file: {url}")
            try:
                with httpx.stream("GET", url, timeout=600, follow_redirects=True) as r:
                    r.raise_for_status()
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_bytes(chunk_size=1 << 20):
                            f.write(chunk)
                tmp.replace(dest)
            finally:
                tmp.unlink(missing_ok=True)
        logger.info(f"Piper voice installed: {voice_id}")

    def delete_piper_voice(self, voice_id: str) -> bool:
        """Remove an installed voice pair. Returns True if anything was deleted."""
        deleted = False
        for suffix in (".onnx", ".onnx.json"):
            path = self.piper_voices_dir / f"{voice_id}{suffix}"
            if path.exists():
                path.unlink()
                deleted = True
        if deleted:
            piper = self._get_piper()
            if piper:
                piper.evict_voice(voice_id)
        return deleted

    def ensure_default_voice(self) -> None:
        """First-startup bootstrap: when the provider is Piper and no voices
        are installed yet, fetch the global default voice. Failures are the
        caller's to log — the service falls back to browser TTS meanwhile."""
        settings = self._load_settings()
        if settings.get("tts_enabled") is False or settings["tts_provider"] != "piper":
            return
        piper = self._get_piper()
        if not piper or not piper.available:
            return  # piper-tts not installed; browser fallback covers it
        if piper.list_voices():
            return
        voice = settings.get("tts_piper_default_voice") or "en_US-lessac-low"
        logger.info(f"Piper voice bootstrap: downloading default voice {voice}...")
        self.download_piper_voice(voice)

    # ── API endpoint ──

    def _synthesize_api(self, text: str, endpoint_id: str, model: str, voice: str, speed: float = 1.0) -> Optional[bytes]:
        from src.database import SessionLocal, ModelEndpoint

        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == endpoint_id).first()
            if not ep:
                logger.error(f"TTS endpoint {endpoint_id} not found")
                return None
            base_url = ep.base_url.rstrip("/")
            api_key = ep.api_key
        finally:
            db.close()

        url = base_url + "/audio/speech"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model,
            "input": text,
            "voice": voice,
            "response_format": "mp3",
            "speed": speed,
        }

        try:
            r = httpx.post(url, json=payload, headers=headers, timeout=60)
            r.raise_for_status()
            logger.info(f"API TTS: {len(r.content)} bytes from {base_url}")
            return r.content
        except Exception as e:
            logger.error(f"API TTS synthesis failed: {e}")
            return None

    # ── Public interface ──

    def synthesize(self, text: str, use_cache: bool = True, owner: str = "") -> Optional[bytes]:
        from services.tts.markdown_to_speech import markdown_to_speech
        text = markdown_to_speech(text)
        if not text:
            return None

        settings = self._load_settings(owner)
        if settings.get("tts_enabled") is False:
            return None
        # Effective provider — a configured-but-unusable local provider has
        # already degraded to "browser" here (synthesis then happens client-side).
        provider, _ = self.effective_provider(settings)
        model = settings["tts_model"]
        voice = settings["tts_voice"]
        speed = _safe_speed(settings.get("tts_speed", "1"))

        if provider in ("disabled", "browser"):
            return None

        if provider == "piper":
            voice = self._resolve_piper_voice(settings)
            if not voice:
                logger.warning("Piper TTS: no voices installed in %s", self.piper_voices_dir)
                return None
        elif provider == "local":
            voice = self._resolve_kokoro_voice(settings)

        if len(text) > 5000:
            text = text[:5000]

        if use_cache:
            key = self._cache_key(text, provider, model, voice, speed)
            cached = self._get_cached(key)
            if cached:
                logger.info(f"TTS cache hit ({len(text)} chars)")
                return cached

        audio_data = None

        if provider == "local":
            kokoro = self._get_kokoro()
            if kokoro and kokoro.available:
                audio_data = kokoro.synthesize_raw(text, voice, speed=speed)
            else:
                logger.warning("Kokoro TTS not available")
                return None
        elif provider == "piper":
            piper = self._get_piper()
            if piper and piper.available:
                # Piper's length_scale stretches duration, so it's the inverse
                # of the user-facing speed multiplier (2x speed = 0.5 scale).
                audio_data = piper.synthesize_raw(text, voice, length_scale=1.0 / speed)
            else:
                logger.warning("Piper TTS not available (pip install piper-tts)")
                return None
        elif provider.startswith("endpoint:"):
            endpoint_id = provider.split(":", 1)[1]
            audio_data = self._synthesize_api(text, endpoint_id, model, voice, speed)
        else:
            logger.error(f"Unknown TTS provider: {provider}")
            return None

        if audio_data and use_cache:
            key = self._cache_key(text, provider, model, voice, speed)
            self._put_cache(key, audio_data)

        return audio_data

    def synthesize_to_base64(self, text: str, owner: str = "") -> Optional[str]:
        import base64
        audio = self.synthesize(text, owner=owner)
        if audio:
            return base64.b64encode(audio).decode("utf-8")
        return None

    def get_stats(self, owner: str = "") -> Dict[str, Any]:
        settings = self._load_settings(owner)
        provider, fallback_reason = self.effective_provider(settings)

        cache_files = list(self.cache_dir.glob("*.wav")) + list(self.cache_dir.glob("*.mp3"))
        cache_size = sum(f.stat().st_size for f in cache_files)

        is_available = provider != "disabled"
        stats = {
            "available": is_available,
            "ready": is_available,
            # The provider actually in effect — clients act on this. The
            # admin-configured value plus the reason it degraded (if it did)
            # are reported alongside so the settings UI can explain itself.
            "provider": provider,
            "configured_provider": settings["tts_provider"],
            "fallback_reason": fallback_reason,
            "model": settings["tts_model"],
            "voice": settings["tts_voice"],
            "speed": _safe_speed(settings.get("tts_speed", "1")),
            "cache_entries": len(cache_files),
            "cache_size_mb": round(cache_size / (1024 * 1024), 2),
        }

        if provider == "local":
            kokoro = self._get_kokoro()
            stats["model"] = f"Kokoro-82M ({kokoro.device.upper()})" if kokoro else "Kokoro-82M"
            stats["voice"] = self._resolve_kokoro_voice(settings)
        elif provider == "piper":
            piper = self._get_piper()
            stats["model"] = "Piper (CPU)"
            stats["voice"] = self._resolve_piper_voice(settings) or settings["tts_voice"]
            stats["voices_installed"] = len(piper.list_voices()) if piper else 0
        elif provider == "browser":
            stats["model"] = "Browser (Web Speech API)"
        elif provider.startswith("endpoint:"):
            stats["endpoint_id"] = provider.split(":", 1)[1]

        return stats


class _KokoroPipeline:
    """Encapsulates the local Kokoro-82M pipeline.

    Runs on CUDA when available, CPU otherwise (the 82M model is fast enough
    for CPU synthesis). Availability is a cheap import probe; the model itself
    loads lazily on first synthesis so stats calls never pay the load cost.
    """

    SAMPLE_RATE = 24000

    def __init__(self):
        self.pipeline = None
        self.device = "cpu"
        self._load_lock = threading.Lock()
        try:
            import torch
            import kokoro  # noqa: F401
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            self.available = True
        except ImportError as e:
            logger.warning(f"Kokoro TTS not available: {e}")
            logger.warning("Install with: pip install kokoro torch soundfile")
            self.available = False

    def _load(self):
        if self.pipeline is not None:
            return self.pipeline
        with self._load_lock:
            if self.pipeline is not None:  # loaded while we waited
                return self.pipeline
            from kokoro import KPipeline
            self.pipeline = KPipeline(lang_code="a", device=self.device, repo_id="hexgrad/Kokoro-82M")
            logger.info(f"Kokoro-82M TTS pipeline loaded on {self.device}")
            return self.pipeline

    def synthesize_raw(self, text: str, voice: str = "af_heart", speed: float = 1.0) -> Optional[bytes]:
        if not self.available:
            return None
        try:
            import numpy as np

            pipeline = self._load()
            chunks = []
            for result in pipeline(text, voice=voice, speed=speed):
                audio = getattr(result, "audio", None)
                if audio is None:
                    continue
                # KPipeline yields torch tensors (on GPU when device=cuda) —
                # move to host memory before treating them as numpy arrays.
                if hasattr(audio, "detach"):
                    audio = audio.detach().cpu().numpy()
                chunks.append(np.asarray(audio, dtype=np.float32))

            if not chunks:
                return None

            full = np.clip(np.concatenate(chunks), -1.0, 1.0)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.SAMPLE_RATE)
                wf.writeframes((full * 32767).astype(np.int16).tobytes())
            return buf.getvalue()
        except Exception as e:
            logger.error(f"Kokoro synthesis failed: {e}", exc_info=True)
            return None


class _PiperPipeline:
    """Encapsulates local Piper TTS (ONNX, CPU-friendly).

    Voices live in data/piper_voices/ as the standard Piper pair:
    <voice-id>.onnx + <voice-id>.onnx.json. Download with e.g.:
        python -m piper.download_voices en_US-lessac-medium --data-dir data/piper_voices
    """

    def __init__(self, voices_dir: Path):
        self.voices_dir = Path(voices_dir)
        self._voices: Dict[str, Any] = {}  # voice id -> loaded PiperVoice
        self._load_lock = threading.Lock()  # avoid duplicate concurrent model loads
        try:
            from piper import PiperVoice  # noqa: F401
            self.available = True
        except ImportError as e:
            logger.warning(f"Piper TTS not available: {e}")
            logger.warning("Install with: pip install piper-tts")
            self.available = False

    def list_voices(self) -> List[Dict[str, str]]:
        """Scan the voices dir for installed *.onnx (+ .onnx.json) pairs."""
        voices = []
        if not self.voices_dir.is_dir():
            return voices
        for onnx in sorted(self.voices_dir.glob("*.onnx")):
            config = onnx.with_suffix(".onnx.json")
            if not config.exists():
                continue
            voice_id = onnx.stem
            language = ""
            try:
                cfg = json.loads(config.read_text(encoding="utf-8"))
                language = (cfg.get("language") or {}).get("name_english") or \
                           (cfg.get("language") or {}).get("code") or ""
            except Exception:
                pass
            voices.append({
                "id": voice_id,
                "name": voice_id.replace("_", " ").replace("-", " — ", 1),
                "language": language,
            })
        return voices

    def _load_voice(self, voice_id: str):
        if voice_id in self._voices:
            return self._voices[voice_id]
        with self._load_lock:
            if voice_id in self._voices:  # loaded while we waited
                return self._voices[voice_id]
            from piper import PiperVoice
            path = self.voices_dir / f"{voice_id}.onnx"
            if not path.exists():
                logger.error(f"Piper voice not found: {path}")
                return None
            voice = PiperVoice.load(str(path))
            self._voices[voice_id] = voice
            logger.info(f"Piper voice loaded: {voice_id}")
            return voice

    def evict_voice(self, voice_id: str):
        """Drop a cached model instance (after the voice files are deleted)."""
        with self._load_lock:
            self._voices.pop(voice_id, None)

    def synthesize_raw(self, text: str, voice_id: str, length_scale: float = 1.0) -> Optional[bytes]:
        if not self.available:
            return None
        try:
            voice = self._load_voice(voice_id)
            if voice is None:
                return None

            syn_config = None
            try:
                from piper import SynthesisConfig
                syn_config = SynthesisConfig(length_scale=length_scale)
            except ImportError:
                pass  # older piper-tts — synthesize at default speed

            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                if syn_config is not None:
                    voice.synthesize_wav(text, wf, syn_config=syn_config)
                else:
                    voice.synthesize_wav(text, wf)
            return buf.getvalue()
        except Exception as e:
            logger.error(f"Piper synthesis failed: {e}", exc_info=True)
            return None


# Module-level singleton
_tts_service = None

def get_tts_service() -> TTSService:
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service
