# services/stt/stt_service.py
"""Multi-provider Speech-to-Text service — dispatches to local Whisper, OpenAI-compatible API, or browser."""

import io
import logging
import httpx
import os
import tempfile
import time
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger(__name__)


# ── Local chunking ──
# Long recordings are split into overlapping windows before local inference,
# so a multi-minute recording transcribes as a series of bounded jobs with
# per-chunk progress logs instead of one opaque call that outlives every
# client timeout. Tuned via:
#   ODYSSEUS_STT_CHUNK_SECONDS (default 120; 0 or negative disables chunking)
#   ODYSSEUS_STT_CHUNK_OVERLAP_SECONDS (default 3; clamped to half a chunk)
# Overlap is naive: a word straddling a boundary may duplicate or clip.
# Documented v1 behavior — the alternative on small hosts is no transcript.
_STT_CHUNK_DEFAULT_SECONDS = 120.0
_STT_CHUNK_DEFAULT_OVERLAP_SECONDS = 3.0
# A trailing window shorter than this merges into the previous one instead
# of running inference for a couple of seconds of audio.
_STT_CHUNK_MIN_TAIL_SECONDS = 15.0
_STT_CHUNK_SAMPLE_RATE = 16000


def _read_seconds_env(name: str, default: float) -> float:
    """Read a float-seconds env var; blank/malformed falls back to default."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(f"Ignoring malformed {name}={raw!r}, using {default}")
        return default


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

    # ── Settings ──

    def _load_settings(self) -> dict:
        from src.settings import load_settings
        saved = load_settings()
        return {
            "stt_enabled": saved.get("stt_enabled", False),
            "stt_provider": saved.get("stt_provider", "disabled"),
            "stt_model": saved.get("stt_model", "base"),
            "stt_language": saved.get("stt_language", ""),
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

    def _get_whisper(self):
        if self._whisper_model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                logger.warning("faster-whisper not installed. Install with: pip install faster-whisper")
                return None
            try:
                settings = self._load_settings()
                model_size = settings.get("stt_model", "base")
                # faster-whisper runs on CTranslate2, not torch. torch is only
                # used (optionally) to detect a CUDA device for acceleration —
                # if it's missing or unusable we just run on CPU. Keeping this
                # probe separate (and tolerant of any failure, e.g. a broken
                # CUDA/torch install that raises OSError on import) means a
                # torch-less or torch-broken machine still does CPU
                # transcription instead of failing with a misleading
                # "faster-whisper not installed" error.
                try:
                    import torch
                    use_cuda = torch.cuda.is_available()
                except Exception:
                    use_cuda = False
                device = "cuda" if use_cuda else "cpu"
                compute_type = "float16" if device == "cuda" else "int8"
                self._whisper_model = WhisperModel(model_size, device=device, compute_type=compute_type)
                logger.info(f"faster-whisper model '{model_size}' loaded on {device}")
            except Exception as e:
                logger.error(f"Failed to load whisper model: {e}")
                return None
        return self._whisper_model

    @staticmethod
    def _chunk_seconds() -> float:
        """Configured window length for local transcription, <=0 disables."""
        return _read_seconds_env("ODYSSEUS_STT_CHUNK_SECONDS", _STT_CHUNK_DEFAULT_SECONDS)

    @staticmethod
    def _chunk_overlap_seconds(chunk: float) -> float:
        """Configured window overlap, clamped to [0, chunk/2]."""
        overlap = _read_seconds_env(
            "ODYSSEUS_STT_CHUNK_OVERLAP_SECONDS", _STT_CHUNK_DEFAULT_OVERLAP_SECONDS
        )
        return min(max(overlap, 0.0), chunk / 2.0) if chunk > 0 else 0.0

    @staticmethod
    def _chunk_spans(duration: float, chunk: float, overlap: float):
        """Split duration into [(start, end)] windows, or None for single-pass.

        Returns None when chunking is disabled, when the audio fits in one
        window, or when only one window would result (short tail merges).
        """
        if chunk <= 0 or duration <= chunk:
            return None
        step = chunk - overlap
        if step <= 0:
            step = chunk
        spans: List[Tuple[float, float]] = []
        start = 0.0
        while True:
            end = start + chunk
            if end >= duration or duration - end < _STT_CHUNK_MIN_TAIL_SECONDS:
                spans.append((start, duration))
                break
            spans.append((start, end))
            start += step
        return spans if len(spans) > 1 else None

    @staticmethod
    def _decode_for_chunking(tmp_path: str):
        """Decode audio to 16kHz mono float32 for windowed transcription.

        Returns the sample sequence, or None when chunking is unavailable or
        the decode fails — callers then use the single-pass file path.
        """
        try:
            from faster_whisper.audio import decode_audio
        except Exception:
            return None
        try:
            return decode_audio(tmp_path, sampling_rate=_STT_CHUNK_SAMPLE_RATE)
        except Exception as e:
            logger.warning(f"STT chunk decode failed ({e}); falling back to single-pass")
            return None

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

            transcribe_start = time.monotonic()
            logger.info(f"Local STT started: {len(audio_bytes)} bytes")
            chunk = self._chunk_seconds()
            spans = None
            decoded = None
            if chunk > 0:
                decoded = self._decode_for_chunking(tmp_path)
                if decoded is not None:
                    spans = self._chunk_spans(
                        len(decoded) / float(_STT_CHUNK_SAMPLE_RATE),
                        chunk,
                        self._chunk_overlap_seconds(chunk),
                    )
            if spans is None:
                segments, info = model.transcribe(tmp_path, **kwargs)
                text = " ".join(seg.text.strip() for seg in segments)
            else:
                logger.info(
                    f"Local STT chunking: {len(spans)} windows "
                    f"({chunk:.0f}s + overlap) for "
                    f"{len(decoded) / float(_STT_CHUNK_SAMPLE_RATE):.1f}s audio"
                )
                chunk_texts = []
                for i, (start, end) in enumerate(spans):
                    chunk_started = time.monotonic()
                    logger.info(
                        f"Local STT chunk {i + 1}/{len(spans)}: audio {start:.1f}s-{end:.1f}s"
                    )
                    s0 = int(start * _STT_CHUNK_SAMPLE_RATE)
                    s1 = int(end * _STT_CHUNK_SAMPLE_RATE)
                    segments, info = model.transcribe(decoded[s0:s1], **kwargs)
                    chunk_texts.append(" ".join(seg.text.strip() for seg in segments))
                    logger.info(
                        f"Local STT chunk {i + 1}/{len(spans)} done: "
                        f"{len(chunk_texts[-1])} chars in "
                        f"{time.monotonic() - chunk_started:.0f}s"
                    )
                text = " ".join(t for t in chunk_texts if t)
                info = None

            if info is not None:
                logger.info(f"Local STT: {len(text)} chars, lang={info.language}, prob={info.language_probability:.2f}")
            else:
                logger.info(
                    f"Local STT done: {len(text)} chars in "
                    f"{time.monotonic() - transcribe_start:.0f}s (chunked)"
                )
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
            # External GPU endpoints can need minutes for long clips; the STT
            # routes run this off the event loop, so a generous budget is safe.
            r = httpx.post(url, headers=headers, files=files, data=data, timeout=180)
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
        }

        if provider == "local":
            whisper = self._get_whisper()
            stats["model_loaded"] = whisper is not None
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
