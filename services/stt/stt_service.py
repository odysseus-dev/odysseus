# services/stt/stt_service.py
"""Multi-provider Speech-to-Text service — dispatches to local Whisper, OpenAI-compatible API, or browser."""

import atexit
import io
import json
import logging
import os
import select
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Optional, Dict, Any

import httpx

logger = logging.getLogger(__name__)

_FW_WORKER_PYTHON = os.environ.get(
    "ODYSSEUS_FW_PYTHON", "/opt/faster-whisper/venv/bin/python"
)
_FW_WORKER_SCRIPT = os.environ.get(
    "ODYSSEUS_FW_WORKER", "/app/services/stt/faster_whisper_worker.py"
)
_FW_WORKER_MODEL = os.environ.get(
    "ODYSSEUS_FW_MODEL", "/opt/faster-whisper/tiny"
)
_FW_REQUEST_TIMEOUT = float(os.environ.get("ODYSSEUS_FW_TIMEOUT", "30"))


class FasterWhisperWorker:
    """Own one long-lived Faster Whisper subprocess.

    The app sends audio file paths over stdin. The worker keeps the model in
    memory, so transcription does not pay a model-load cost on every request.
    No port is opened and no audio transcript is written to disk.
    """

    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._next_id = 1
        self._ready_model: Optional[str] = None
        self._closed = False
        atexit.register(self.close)

    def available(self) -> bool:
        return (
            os.path.isfile(_FW_WORKER_PYTHON)
            and os.path.isfile(_FW_WORKER_SCRIPT)
            and os.path.isdir(_FW_WORKER_MODEL)
        )

    def is_ready(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def ready_model(self) -> Optional[str]:
        with self._lock:
            return self._ready_model

    def warm_up(self) -> bool:
        if not self.available():
            return False
        try:
            with self._lock:
                self._ensure_started_locked()
            return True
        except Exception as exc:
            logger.error("Faster Whisper worker warm-up failed: %s", exc)
            return False

    def request(self, audio_path: str, language: str = "") -> Dict[str, Any]:
        with self._lock:
            if self._closed:
                raise RuntimeError("Faster Whisper worker is closed")
            try:
                self._ensure_started_locked()

                request_id = self._next_id
                self._next_id += 1
                payload = {
                    "id": request_id,
                    "path": audio_path,
                    "language": language or None,
                }
                self._proc.stdin.write(json.dumps(payload) + "\n")
                self._proc.stdin.flush()

                response = json.loads(self._readline_locked(_FW_REQUEST_TIMEOUT))
                if response.get("id") != request_id:
                    raise RuntimeError("Faster Whisper worker response id mismatch")
                if not response.get("ok"):
                    raise RuntimeError(response.get("error") or "Faster Whisper worker error")
                return response
            except Exception:
                self._stop_locked()
                raise

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._stop_locked()

    def _ensure_started_locked(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["ODYSSEUS_FW_MODEL"] = _FW_WORKER_MODEL
        env["ODYSSEUS_FW_CPU_THREADS"] = env.get("ODYSSEUS_FW_CPU_THREADS", "4")

        self._proc = subprocess.Popen(
            [_FW_WORKER_PYTHON, _FW_WORKER_SCRIPT],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            close_fds=True,
            cwd="/app",
            env=env,
        )
        self._ready_model = None

        ready = json.loads(self._readline_locked(15.0))
        if not ready.get("ready"):
            raise RuntimeError("Faster Whisper worker did not become ready")
        self._ready_model = str(ready.get("model") or _FW_WORKER_MODEL)
        logger.info(
            "Faster Whisper worker ready: model=%s version=%s",
            self._ready_model,
            ready.get("version"),
        )

    def _readline_locked(self, timeout: float) -> str:
        if self._proc is None or self._proc.stdout is None:
            raise RuntimeError("Faster Whisper worker is not running")
        readable, _, _ = select.select([self._proc.stdout], [], [], timeout)
        if not readable:
            raise TimeoutError("Faster Whisper worker timed out")
        line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError("Faster Whisper worker exited")
        return line

    def _stop_locked(self) -> None:
        proc = self._proc
        self._proc = None
        self._ready_model = None
        if proc is None:
            return
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except Exception:
                pass
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass


class STTService:
    """Multi-provider STT service.

    Reads provider config from data/settings.json on each call.
    Providers:
      "disabled"        — no STT
      "browser"         — client-side Web Speech API (no server transcription)
      "local"           — isolated Faster Whisper worker process
      "endpoint:<id>"   — OpenAI-compatible /audio/transcriptions via ModelEndpoint
    """

    def __init__(self):
        self._worker = FasterWhisperWorker()

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
            return self._worker.available()
        if provider.startswith("endpoint:"):
            return True  # assume reachable
        return False

    def warm_up(self) -> None:
        """Load the local model once at app start to keep first use fast."""
        settings = self._load_settings()
        if settings.get("stt_enabled") and settings["stt_provider"] == "local":
            self._worker.warm_up()

    # ── Local Whisper worker ──

    def _transcribe_local(self, audio_bytes: bytes, language: str = "") -> Optional[str]:
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            response = self._worker.request(tmp_path, language)
            text = str(response.get("text") or "").strip()
            logger.info(
                "Local STT: %d chars, lang=%s, prob=%.2f",
                len(text),
                response.get("language"),
                float(response.get("language_probability") or 0.0),
            )
            return text
        except Exception as e:
            logger.error("Local STT transcription failed: %s", e)
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
                logger.error("STT endpoint %s not found", endpoint_id)
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
            logger.info("API STT: %d chars from %s", len(text), base_url)
            return text
        except Exception as e:
            logger.error("API STT transcription failed: %s", e)
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
            logger.error("Unknown STT provider: %s", provider)
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
            stats["model_loaded"] = self._worker.is_ready()
            stats["worker_model"] = self._worker.ready_model() or _FW_WORKER_MODEL
        elif provider == "browser":
            stats["model"] = "Browser (Web Speech API)"
        elif provider.startswith("endpoint:"):
            stats["endpoint_id"] = provider.split(":", 1)[1]

        return stats

    def close(self) -> None:
        self._worker.close()


# Module-level singleton
_stt_service = None

def get_stt_service() -> STTService:
    global _stt_service
    if _stt_service is None:
        _stt_service = STTService()
    return _stt_service
