# services/stt/stt_service.py
"""Multi-provider Speech-to-Text service.

Providers:
  "disabled"        — no STT
  "browser"         — client-side Web Speech API (server not involved)
  "local"           — faster-whisper running locally on CPU/GPU
  "endpoint:<id>"   — any OpenAI-compatible /audio/transcriptions endpoint
"""

import io
import logging
import httpx
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class STTService:
    def __init__(self):
        self._whisper_model = None   # lazy-init; reset when model name changes
        self._whisper_model_name: str = ""

    # ── Settings ──────────────────────────────────────────────────────────────

    def _load_settings(self) -> dict:
        from src.settings import load_settings
        saved = load_settings()
        return {
            "stt_enabled":      saved.get("stt_enabled", False),
            "stt_provider":     saved.get("stt_provider", "disabled"),
            "stt_model":        saved.get("stt_model", "base"),
            "stt_language":     saved.get("stt_language", ""),
        }

    @property
    def available(self) -> bool:
        s = self._load_settings()
        if not s.get("stt_enabled"):
            return False
        p = s["stt_provider"]
        if p in ("disabled", "browser"):
            return False
        if p == "local":
            return self._get_whisper(s["stt_model"]) is not None
        if p.startswith("endpoint:"):
            return True   # assume reachable; fail at call time
        return False

    # ── Local Whisper ─────────────────────────────────────────────────────────

    def _get_whisper(self, model_size: str = "base"):
        # Reload if model size changed
        if self._whisper_model is not None and self._whisper_model_name != model_size:
            self._whisper_model = None

        if self._whisper_model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                logger.warning(
                    "faster-whisper not installed. "
                    "Run: pip install faster-whisper"
                )
                return None
            try:
                try:
                    import torch
                    use_cuda = torch.cuda.is_available()
                except Exception:
                    use_cuda = False
                device = "cuda" if use_cuda else "cpu"
                compute_type = "float16" if device == "cuda" else "int8"
                self._whisper_model = WhisperModel(
                    model_size, device=device, compute_type=compute_type
                )
                self._whisper_model_name = model_size
                logger.info(
                    f"faster-whisper '{model_size}' loaded on {device} ({compute_type})"
                )
            except Exception as e:
                logger.error(f"Failed to load whisper model '{model_size}': {e}")
                return None
        return self._whisper_model

    def _normalize_language(self, lang: str) -> str:
        """Return a valid ISO 639-1 language code, or '' for auto-detect.

        faster-whisper (and Groq) only accept short codes like 'en', 'hi', 'fr'.
        If the user typed a full name like 'English' we silently fall back to
        auto-detect rather than crashing.
        """
        lang = (lang or "").strip()
        if not lang:
            return ""
        # Accept only short codes (2–3 chars, letters only, e.g. en / zh / haw / yue)
        if len(lang) <= 4 and lang.isalpha():
            return lang.lower()
        # Full name like "English" — log a warning and auto-detect
        logger.warning(
            f"STT: '{lang}' is not a valid ISO 639-1 code — using auto-detect instead. "
            "Set a short code like 'en', 'hi', 'fr' in Settings."
        )
        return ""

    def _transcribe_local(self, audio_bytes: bytes, model_size: str = "base", language: str = "") -> Optional[str]:
        language = self._normalize_language(language)
        model = self._get_whisper(model_size)
        if not model:
            return None
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            kwargs: dict = {}
            if language:
                kwargs["language"] = language

            segments, info = model.transcribe(tmp_path, **kwargs)
            text = " ".join(seg.text.strip() for seg in segments).strip()
            logger.info(
                f"Local STT: {len(text)} chars | lang={info.language} "
                f"({info.language_probability:.0%})"
            )
            return text or None
        except Exception as e:
            logger.error(f"Local STT failed: {e}", exc_info=True)
            return None
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

    # ── Generic OpenAI-compatible endpoint ────────────────────────────────────

    def _transcribe_endpoint(
        self,
        audio_bytes: bytes,
        endpoint_id: str,
        model: str,
        language: str = "",
    ) -> Optional[str]:
        language = self._normalize_language(language)
        from src.database import SessionLocal, ModelEndpoint

        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == endpoint_id).first()
            if not ep:
                logger.error(f"STT endpoint '{endpoint_id}' not found")
                return None
            base_url = ep.base_url.rstrip("/")
            api_key  = ep.api_key or ""
        finally:
            db.close()

        url = f"{base_url}/audio/transcriptions"
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # If base_url belongs to Groq, default to their recommended Whisper model instead of whisper-1
        default_model = "whisper-large-v3-turbo" if "groq.com" in base_url else "whisper-1"

        files = {"file": ("audio.webm", io.BytesIO(audio_bytes), "audio/webm")}
        data: dict = {"model": model.strip() if (model and model.strip()) else default_model}
        if language:
            data["language"] = language

        try:
            r = httpx.post(url, headers=headers, files=files, data=data, timeout=60)
            r.raise_for_status()
            text = r.json().get("text", "").strip()
            logger.info(f"Endpoint STT ({base_url}): {len(text)} chars")
            return text or None
        except Exception as e:
            logger.error(f"Endpoint STT failed: {e}")
            raise

    # ── Public interface ───────────────────────────────────────────────────────

    def transcribe(self, audio_bytes: bytes) -> Optional[str]:
        s = self._load_settings()
        if not s.get("stt_enabled", False):
            return None

        provider = s["stt_provider"]
        model    = s["stt_model"]
        language = s.get("stt_language", "")

        if provider in ("disabled", "browser", None, ""):
            return None

        if provider == "local":
            return self._transcribe_local(audio_bytes, model or "base", language)

        if provider.startswith("endpoint:"):
            endpoint_id = provider.split(":", 1)[1]
            return self._transcribe_endpoint(audio_bytes, endpoint_id, model, language)

        logger.error(f"Unknown STT provider: {provider!r}")
        return None

    def get_stats(self) -> Dict[str, Any]:
        s = self._load_settings()
        provider    = s["stt_provider"]
        stt_enabled = bool(s.get("stt_enabled", False))

        # Always return the real provider so the client knows what mode to use
        stats: Dict[str, Any] = {
            "available":    stt_enabled and provider not in ("disabled", "browser", "", None),
            "provider":     provider,
            "enabled":      stt_enabled,
            "model":        s["stt_model"],
            "language":     s.get("stt_language", ""),
        }

        if provider == "local":
            whisper = self._get_whisper(s["stt_model"])
            stats["model_loaded"] = whisper is not None
        elif provider.startswith("endpoint:"):
            stats["endpoint_id"] = provider.split(":", 1)[1]

        return stats


# Module-level singleton
_stt_service: Optional[STTService] = None


def get_stt_service() -> STTService:
    global _stt_service
    if _stt_service is None:
        _stt_service = STTService()
    return _stt_service
