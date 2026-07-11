# src/tts_service.py
"""Multi-provider TTS service — dispatches to local Kokoro, OpenAI-compatible API, or browser."""

import io
import wave
import logging
import hashlib
import httpx
from pathlib import Path
from typing import Optional, Dict, Any

from src.constants import TTS_CACHE_DIR

logger = logging.getLogger(__name__)


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
      "endpoint:<id>"   — OpenAI-compatible /audio/speech via ModelEndpoint
    """

    def __init__(self, cache_dir: str = TTS_CACHE_DIR):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._kokoro = None  # lazy-init
        self._piper = None  # lazy-init
        self._silero = None  # lazy-init

    # ── Settings ──

    def _load_settings(self) -> dict:
        from src.settings import load_settings
        saved = load_settings()
        return {
            "tts_enabled": saved.get("tts_enabled", True),
            "tts_provider": saved.get("tts_provider", "disabled"),
            "tts_model": saved.get("tts_model", "tts-1"),
            "tts_voice": saved.get("tts_voice", "alloy"),
            "tts_speed": saved.get("tts_speed", "1"),
        }

    @property
    def available(self) -> bool:
        settings = self._load_settings()
        if settings.get("tts_enabled") is False:
            return False
        provider = settings["tts_provider"]
        if provider == "disabled":
            return False
        if provider == "browser":
            return True  # handled client-side
        if provider == "local":
            kokoro = self._get_kokoro()
            return kokoro is not None and kokoro.available
        if provider == "piper":
            piper = self._get_piper()
            return piper is not None and piper.available
        if provider == "silero":
            silero = self._get_silero()
            return silero is not None and silero.available
        if isinstance(provider, str) and provider.startswith("endpoint:"):
            return True  # assume reachable; errors surface at synthesis time
        return False

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

    # ── Piper (local) ──

    def _get_piper(self):
        if self._piper is None:
            self._piper = _PiperPipeline()
        return self._piper

    # ── Silero (local) ──

    def _get_silero(self):
        if self._silero is None:
            self._silero = _SileroPipeline()
        return self._silero

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

    def synthesize(self, text: str, use_cache: bool = True) -> Optional[bytes]:
        settings = self._load_settings()
        if settings.get("tts_enabled") is False:
            return None
        provider = settings["tts_provider"]
        model = settings["tts_model"]
        voice = settings["tts_voice"]
        speed = _safe_speed(settings.get("tts_speed", "1"))

        if provider in ("disabled", "browser"):
            return None

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
                audio_data = kokoro.synthesize_raw(text, voice)
            else:
                logger.warning("Kokoro TTS not available")
                return None
        elif provider == "piper":
            piper = self._get_piper()
            if piper and piper.available:
                audio_data = piper.synthesize_raw(text, voice, speed)
            else:
                logger.warning("Piper TTS not available")
                return None
        elif provider == "silero":
            silero = self._get_silero()
            if silero and silero.available:
                audio_data = silero.synthesize_raw(text, voice, speed)
            else:
                logger.warning("Silero TTS not available")
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

    def synthesize_to_base64(self, text: str) -> Optional[str]:
        import base64
        audio = self.synthesize(text)
        if audio:
            return base64.b64encode(audio).decode("utf-8")
        return None

    def set_voice(self, voice: str):
        """Legacy no-op — voice is now managed via admin settings."""

    def get_stats(self) -> Dict[str, Any]:
        settings = self._load_settings()
        provider = settings["tts_provider"]
        tts_enabled = settings.get("tts_enabled", True)

        cache_files = list(self.cache_dir.glob("*.wav")) + list(self.cache_dir.glob("*.mp3"))
        cache_size = sum(f.stat().st_size for f in cache_files)

        is_available = self.available and tts_enabled
        stats = {
            "available": is_available,
            "ready": is_available,
            "provider": provider,
            "model": settings["tts_model"],
            "voice": settings["tts_voice"],
            "speed": _safe_speed(settings.get("tts_speed", "1")),
            "cache_entries": len(cache_files),
            "cache_size_mb": round(cache_size / (1024 * 1024), 2),
        }

        if provider == "local":
            kokoro = self._get_kokoro()
            if kokoro and kokoro.available:
                stats["model"] = f"Kokoro-82M ({kokoro._device_label})"
            else:
                stats["model"] = "Kokoro (not loaded)"
        elif provider == "piper":
            piper = self._get_piper()
            if piper and piper.available:
                stats["model"] = f"Piper ({piper._device_label})"
                stats["voice"] = piper._voice_name or "en_US-lessac-medium"
            else:
                stats["model"] = "Piper (not loaded)"
        elif provider == "silero":
            silero = self._get_silero()
            if silero and silero.available:
                stats["model"] = f"Silero v5 ({silero._device_label})"
                stats["voice"] = silero._voice or "kseniya"
                stats["speakers"] = silero.model.speakers if silero.model else []
            else:
                stats["model"] = "Silero (not loaded)"
        elif provider == "browser":
            stats["model"] = "Browser (Web Speech API)"
        elif provider.startswith("endpoint:"):
            stats["endpoint_id"] = provider.split(":", 1)[1]

        return stats


class _KokoroPipeline:
    """Encapsulates the Kokoro-82M local GPU pipeline."""

    def __init__(self):
        self.pipeline = None
        self.available = False
        self.device = None
        self._init()

    def _init(self):
        try:
            import torch
            from kokoro import KPipeline

            if torch.cuda.is_available():
                self.device = torch.device("cuda:0")
                with torch.cuda.device(0):
                    self.pipeline = KPipeline(lang_code="a")
                    if hasattr(self.pipeline, "model"):
                        self.pipeline.model = self.pipeline.model.to(self.device)
                self._device_label = "GPU"
                logger.info("Kokoro-82M TTS pipeline loaded on GPU")
            else:
                self.device = torch.device("cpu")
                self.pipeline = KPipeline(lang_code="a")
                self._device_label = "CPU"
                logger.warning("Kokoro-82M TTS loaded on CPU — synthesis will be slower (~2-5s/sentence)")

            self.available = True
        except ImportError as e:
            logger.warning(f"Kokoro TTS not available: {e}")
            logger.warning("Install with: pip install kokoro soundfile")
        except Exception as e:
            logger.error(f"Kokoro init failed: {e}", exc_info=True)

    def synthesize_raw(self, text: str, voice: str = "af_heart") -> Optional[bytes]:
        if not self.available:
            return None
        try:
            import numpy as np

            chunks = []
            for _, _, audio in self.pipeline(text, voice=voice):
                chunks.append(audio)

            if not chunks:
                return None

            full = np.concatenate(chunks)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)
                wf.writeframes((full * 32767).astype(np.int16).tobytes())
            return buf.getvalue()
        except Exception as e:
            logger.error(f"Kokoro synthesis failed: {e}", exc_info=True)
            return None


class _PiperPipeline:
    """Encapsulates Piper TTS — fast local TTS using ONNX models.

    Supports many languages out of the box (Russian, English, etc.).
    Voice models are .onnx files downloaded from HuggingFace.
    """

    MODELS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "piper_voices"

    def __init__(self):
        self.voice = None
        self.available = False
        self._voice_name = None
        self._device_label = "CPU"
        self._init()

    def _init(self):
        try:
            from piper import PiperVoice

            self.MODELS_DIR.mkdir(parents=True, exist_ok=True)

            # Find first .onnx model in the voices directory
            models = sorted(self.MODELS_DIR.glob("*.onnx"))
            if not models:
                logger.warning("Piper TTS: no voice models found in %s", self.MODELS_DIR)
                logger.warning("Download with: python3 -m piper.download_voices --output_dir %s ru_RU-irina-medium en_US-lessac-medium", self.MODELS_DIR)
                return

            model_path = models[0]
            self._voice_name = model_path.stem
            self.voice = PiperVoice.load(str(model_path))
            self.available = True
            logger.info(f"Piper TTS loaded: {self._voice_name}")
        except ImportError as e:
            logger.warning(f"Piper TTS not available: {e}")
            logger.warning("Install with: pip install piper-tts")
        except Exception as e:
            logger.error(f"Piper init failed: {e}", exc_info=True)

    def synthesize_raw(self, text: str, voice: str = "", speed: float = 1.0) -> Optional[bytes]:
        if not self.available:
            return None
        try:
            import wave as wave_mod

            # If a specific voice model is requested and different from loaded, try to load it
            if voice and voice != self._voice_name:
                model_path = self.MODELS_DIR / f"{voice}.onnx"
                if model_path.exists():
                    from piper import PiperVoice
                    self.voice = PiperVoice.load(str(model_path))
                    self._voice_name = voice
                else:
                    logger.warning(f"Piper voice '{voice}' not found, using {self._voice_name}")

            buf = io.BytesIO()
            with wave_mod.open(buf, "wb") as wf:
                self.voice.synthesize_wav(text, wf)
            return buf.getvalue()
        except Exception as e:
            logger.error(f"Piper synthesis failed: {e}", exc_info=True)
            return None


class _SileroPipeline:
    """Encapsulates Silero TTS — high-quality local TTS using PyTorch models.

    Supports Russian and English with multiple speakers.
    Models are loaded via torch.hub from snakers4/silero-models.
    """

    MODELS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "silero_models"

    def __init__(self):
        self.model = None
        self.available = False
        self._device_label = "CPU"
        self._voice = "kseniya"
        self._sample_rate = 48000
        self._init()

    def _init(self):
        try:
            import torch
            import os

            os.environ.setdefault("TORCH_HOME", "/app/data/torch_cache")

            self.model, _ = torch.hub.load(
                repo_or_dir='snakers4/silero-models',
                model='silero_tts',
                language='ru',
                speaker='v5_ru',
                trust_repo=True,
            )
            self._voice = "kseniya"
            self._device_label = "CPU"
            self.available = True
            logger.info(f"Silero TTS loaded (v5_ru, speakers: {self.model.speakers})")
        except ImportError as e:
            logger.warning(f"Silero TTS not available: {e}")
            logger.warning("Install with: pip install torch scipy")
        except Exception as e:
            logger.error(f"Silero init failed: {e}", exc_info=True)

    def synthesize_raw(self, text: str, voice: str = "", speed: float = 1.0) -> Optional[bytes]:
        if not self.available:
            return None
        try:
            import torch
            import numpy as np

            speaker = voice if voice and voice in self.model.speakers else self._voice

            audio = self.model.apply_tts(
                text=text,
                speaker=speaker,
                sample_rate=self._sample_rate,
            )

            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self._sample_rate)
                wf.writeframes((audio.numpy() * 32767).astype(np.int16).tobytes())
            return buf.getvalue()
        except Exception as e:
            logger.error(f"Silero synthesis failed: {e}", exc_info=True)
            return None


# Module-level singleton
_tts_service = None

def get_tts_service() -> TTSService:
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service
