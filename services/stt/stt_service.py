# services/stt/stt_service.py
"""Multi-provider Speech-to-Text service — dispatches to local Whisper, OpenAI-compatible API, or browser."""

import io
import logging
import os
import platform
import httpx
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class STTService:
    """Multi-provider STT service.

    Reads provider config from data/settings.json on each call.
    Providers:
      "disabled"        — no STT
      "browser"         — client-side Web Speech API (no server transcription)
      "local"           — faster-whisper on CPU/GPU
      "endpoint:<id>"   — OpenAI-compatible /audio/transcriptions via ModelEndpoint
    """

    def __init__(self):
        self._whisper_model = None  # lazy-init
        self._whisper_config = None

    # ── Settings ──

    def _load_settings(self) -> dict:
        from src.settings import load_settings
        saved = load_settings()
        return {
            "stt_enabled": saved.get("stt_enabled", False),
            "stt_provider": saved.get("stt_provider", "disabled"),
            "stt_model": saved.get("stt_model", "base"),
            "stt_language": saved.get("stt_language", ""),
            "stt_device": os.getenv("ODYSSEUS_STT_DEVICE") or saved.get("stt_device", "auto"),
            "stt_compute_type": os.getenv("ODYSSEUS_STT_COMPUTE_TYPE") or saved.get("stt_compute_type", "auto"),
        }

    @property
    def available(self) -> bool:
        settings = self._load_settings()
        if settings.get("stt_enabled") is False:
            return False
        provider = settings["stt_provider"]
        if provider == "disabled":
            return False
        if provider == "browser":
            return True  # handled client-side
        if provider == "local":
            return self._get_whisper() is not None
        if provider.startswith("endpoint:"):
            return True  # assume reachable
        return False

    # ── Local Whisper ──

    def _resolve_local_backend(self, settings: dict) -> tuple[str, str]:
        configured_device = str(settings.get("stt_device") or "auto").strip().lower()
        configured_compute = str(settings.get("stt_compute_type") or "auto").strip().lower()

        if configured_device == "auto":
            try:
                import torch
                use_cuda = torch.cuda.is_available()
            except Exception:
                use_cuda = False
            device = "cuda" if use_cuda else "cpu"
        else:
            device = configured_device

        if configured_compute == "auto":
            if device == "cuda":
                compute_type = "float16"
            elif platform.system() == "Darwin" and platform.machine() == "arm64":
                compute_type = "int8_float32"
            else:
                compute_type = "int8"
        else:
            compute_type = configured_compute

        return device, compute_type

    def _whisper_init_kwargs(self) -> dict:
        kwargs = {}
        for env_name, arg_name in (
            ("ODYSSEUS_STT_CPU_THREADS", "cpu_threads"),
            ("ODYSSEUS_STT_NUM_WORKERS", "num_workers"),
        ):
            raw_value = os.getenv(env_name, "").strip()
            if not raw_value:
                continue
            try:
                value = int(raw_value)
            except ValueError:
                logger.warning("Ignoring invalid %s=%r; expected integer", env_name, raw_value)
                continue
            if value > 0:
                kwargs[arg_name] = value
        return kwargs

    def _get_whisper(self):
        settings = self._load_settings()
        model_size = settings.get("stt_model", "base")
        device, compute_type = self._resolve_local_backend(settings)
        init_kwargs = self._whisper_init_kwargs()
        config = (model_size, device, compute_type, tuple(sorted(init_kwargs.items())))

        if self._whisper_model is None or self._whisper_config != config:
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                logger.warning("faster-whisper not installed. Install with: pip install faster-whisper")
                return None
            try:
                self._whisper_model = WhisperModel(
                    model_size,
                    device=device,
                    compute_type=compute_type,
                    **init_kwargs,
                )
                self._whisper_config = config
                logger.info(
                    "faster-whisper model '%s' loaded on %s with %s",
                    model_size,
                    device,
                    compute_type,
                )
            except Exception as e:
                if compute_type != "int8" and settings.get("stt_compute_type", "auto") == "auto":
                    try:
                        fallback_config = (model_size, device, "int8", tuple(sorted(init_kwargs.items())))
                        self._whisper_model = WhisperModel(
                            model_size,
                            device=device,
                            compute_type="int8",
                            **init_kwargs,
                        )
                        self._whisper_config = fallback_config
                        logger.warning(
                            "faster-whisper float16 load failed on %s; fell back to int8: %s",
                            device,
                            e,
                        )
                        return self._whisper_model
                    except Exception as fallback_error:
                        logger.error(f"Failed to load whisper model after int8 fallback: {fallback_error}")
                        return None
                logger.error(f"Failed to load whisper model: {e}")
                return None
        return self._whisper_model

    def _transcribe_local(self, audio_bytes: bytes, language: str = "") -> Optional[str]:
        model = self._get_whisper()
        if not model:
            return None
        tmp_path = None
        try:
            # Write to temp file (faster-whisper needs a file path or file-like)
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            kwargs = {}
            if language:
                kwargs["language"] = language

            segments, info = model.transcribe(tmp_path, **kwargs)
            text = " ".join(seg.text.strip() for seg in segments)

            logger.info(f"Local STT: {len(text)} chars, lang={info.language}, prob={info.language_probability:.2f}")
            return text
        except Exception as e:
            logger.error(f"Local STT transcription failed: {e}", exc_info=True)
            return None
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

    # ── API endpoint ──

    def _transcribe_api(self, audio_bytes: bytes, endpoint_id: str, model: str, language: str = "") -> Optional[str]:
        from src.database import SessionLocal, ModelEndpoint

        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == endpoint_id).first()
            if not ep:
                logger.error(f"STT endpoint {endpoint_id} not found")
                return None
            base_url = ep.base_url.rstrip("/")
            api_key = ep.api_key
        finally:
            db.close()

        url = base_url + "/audio/transcriptions"
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        files = {"file": ("audio.webm", io.BytesIO(audio_bytes), "audio/webm")}
        data = {"model": model or "whisper-1"}
        if language:
            data["language"] = language

        try:
            r = httpx.post(url, headers=headers, files=files, data=data, timeout=60)
            r.raise_for_status()
            result = r.json()
            text = result.get("text", "")
            logger.info(f"API STT: {len(text)} chars from {base_url}")
            return text
        except Exception as e:
            logger.error(f"API STT transcription failed: {e}")
            return None

    # ── Public interface ──

    def transcribe(self, audio_bytes: bytes) -> Optional[str]:
        settings = self._load_settings()
        if settings.get("stt_enabled") is False:
            return None
        provider = settings["stt_provider"]
        model = settings["stt_model"]
        language = settings.get("stt_language", "")

        if provider in ("disabled", "browser"):
            return None

        if provider == "local":
            return self._transcribe_local(audio_bytes, language)
        elif provider.startswith("endpoint:"):
            endpoint_id = provider.split(":", 1)[1]
            return self._transcribe_api(audio_bytes, endpoint_id, model, language)
        else:
            logger.error(f"Unknown STT provider: {provider}")
            return None

    def get_stats(self) -> Dict[str, Any]:
        settings = self._load_settings()
        provider = settings["stt_provider"]
        stt_enabled = settings.get("stt_enabled", False)
        # If toggle is off, report as disabled
        effective_provider = provider if stt_enabled else "disabled"

        stats = {
            "available": self.available and stt_enabled,
            "provider": effective_provider,
            "model": settings["stt_model"],
            "language": settings.get("stt_language", ""),
            "device": settings.get("stt_device", "auto"),
            "compute_type": settings.get("stt_compute_type", "auto"),
        }

        if provider == "local":
            whisper = self._get_whisper()
            stats["model_loaded"] = whisper is not None
            resolved_device, resolved_compute = self._resolve_local_backend(settings)
            stats["resolved_device"] = resolved_device
            stats["resolved_compute_type"] = resolved_compute
        elif provider == "browser":
            stats["model"] = "Browser (Web Speech API)"
        elif provider.startswith("endpoint:"):
            stats["endpoint_id"] = provider.split(":", 1)[1]

        return stats


# Module-level singleton
_stt_service = None

def get_stt_service() -> STTService:
    global _stt_service
    if _stt_service is None:
        _stt_service = STTService()
    return _stt_service
