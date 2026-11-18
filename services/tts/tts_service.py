# src/tts_service.py
"""Multi-provider TTS service — dispatches to browser, OpenAI-compatible API,
native xAI TTS, or OpenRouter GPT Audio chat completions."""

import os
import re
import logging
import hashlib
import httpx
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import urlparse, urlunparse

from src.constants import TTS_CACHE_DIR

logger = logging.getLogger(__name__)


class TTSError(Exception):
    """User-facing TTS failure with a safe, actionable message."""


_OPENROUTER_PCM16_SAMPLE_RATE = 24000
_OPENROUTER_PCM16_CHANNELS = 1
_OPENROUTER_PCM16_SAMPLE_WIDTH = 2


def _pcm16_to_wav(pcm: bytes, sample_rate: int = _OPENROUTER_PCM16_SAMPLE_RATE,
                  channels: int = _OPENROUTER_PCM16_CHANNELS) -> bytes:
    """Wrap raw little-endian signed PCM16 bytes in a playable WAV container.

    OpenRouter streams GPT Audio as headerless ``pcm16`` chunks. Browsers need
    a container, so synthesize the RIFF/WAVE header around the accumulated
    samples. Dangling partial frames are dropped rather than producing an
    invalid WAV data chunk.
    """
    import io
    import wave

    frame_width = channels * _OPENROUTER_PCM16_SAMPLE_WIDTH
    if frame_width <= 0:
        raise TTSError("OpenRouter GPT Audio returned an invalid PCM format.")
    whole_frames = len(pcm) - (len(pcm) % frame_width)
    pcm = pcm[:whole_frames]

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(_OPENROUTER_PCM16_SAMPLE_WIDTH)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()


def _speech_url(base_url: str) -> str:
    """Resolve a configured/custom base URL to an OpenAI-compatible
    /audio/speech URL. Accepts either a base (``.../v1``) or the full
    endpoint (``.../v1/audio/speech``)."""
    url = (base_url or "").strip()
    if not url:
        raise TTSError("No TTS endpoint URL is configured.")
    if not url.lower().startswith(("http://", "https://")):
        raise TTSError("TTS endpoint URL must start with http:// or https://.")
    url = url.rstrip("/")
    if url.lower().endswith("/audio/speech"):
        return url
    return url + "/audio/speech"


def _xai_tts_url(base_url: str) -> str:
    """Resolve a configured/custom base URL to xAI's native /v1/tts URL.

    Accepts a host root, a ``.../v1`` base, an xAI native URL, or an
    OpenAI-compatible ``.../v1/audio/speech`` URL that should be redirected to
    the native endpoint.
    """
    url = (base_url or "").strip()
    if not url:
        raise TTSError("No TTS endpoint URL is configured.")
    if not url.lower().startswith(("http://", "https://")):
        raise TTSError("TTS endpoint URL must start with http:// or https://.")

    parsed = urlparse(url)
    path = (parsed.path or "").rstrip("/")
    lower = path.lower()
    if lower.endswith("/audio/speech"):
        path = path[: -len("/audio/speech")]
        lower = path.lower()
    if lower.endswith("/tts"):
        new_path = path
    elif lower.endswith("/v1"):
        new_path = path + "/tts"
    elif not path:
        new_path = "/v1/tts"
    else:
        new_path = path + "/v1/tts"
    return urlunparse((parsed.scheme, parsed.netloc, new_path, "", "", ""))


def _host_is_xai(url: str) -> bool:
    try:
        host = (urlparse((url or "").strip()).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return host == "x.ai" or host.endswith(".x.ai")


def _is_xai_endpoint(*hints: Optional[str]) -> bool:
    """Return True for xAI/Grok endpoints detected by host or metadata hints."""
    for hint in hints:
        text = str(hint or "").strip().lower()
        if not text:
            continue
        if _host_is_xai(text):
            return True
        if "x-ai/" in text or re.search(r"(^|[^a-z0-9])(grok|xai|x-ai)([^a-z0-9]|$)", text):
            return True
    return False


def _host_is_openrouter(url: str) -> bool:
    try:
        host = (urlparse((url or "").strip()).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return host == "openrouter.ai" or host.endswith(".openrouter.ai")


def _is_gpt_audio_model(model: Optional[str]) -> bool:
    """OpenRouter exposes OpenAI GPT Audio through chat completions."""
    text = str(model or "").strip().lower()
    return "gpt-audio" in text and not text.startswith("google/lyria")


def _is_openrouter_audio_endpoint(url: str, model: Optional[str]) -> bool:
    """Return True only for OpenRouter + an OpenAI GPT Audio model.

    OpenRouter's audio-capable chat models do not implement
    ``/audio/speech``; they return base64 audio from ``/chat/completions``.
    Music-generation models such as ``google/lyria-*`` are deliberately not
    treated as speech models.
    """
    return _host_is_openrouter(url) and _is_gpt_audio_model(model)


def _chat_completions_url(base_url: str) -> str:
    """Resolve a configured base URL to OpenRouter's chat-completions URL."""
    url = (base_url or "").strip()
    if not url:
        raise TTSError("No TTS endpoint URL is configured.")
    if not url.lower().startswith(("http://", "https://")):
        raise TTSError("TTS endpoint URL must start with http:// or https://.")

    parsed = urlparse(url)
    path = (parsed.path or "").rstrip("/")
    lower = path.lower()
    if lower.endswith("/chat/completions"):
        new_path = path
    elif lower.endswith("/audio/speech"):
        path = path[: -len("/audio/speech")]
        new_path = path + "/chat/completions"
    elif lower.endswith("/v1") or lower.endswith("/api/v1"):
        new_path = path + "/chat/completions"
    elif not path:
        new_path = "/api/v1/chat/completions"
    else:
        new_path = path + "/chat/completions"
    return urlunparse((parsed.scheme, parsed.netloc, new_path, "", "", ""))


def _xai_speed(value: float) -> float:
    """xAI accepts 0.7-1.5; clamp the app's wider speed options."""
    return min(1.5, max(0.7, value))


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
      "endpoint:<id>"   — OpenAI-compatible /audio/speech via ModelEndpoint,
                          native xAI /v1/tts for xAI/Grok, or OpenRouter chat
                          completions for OpenAI GPT Audio models
      "custom"          — OpenAI-compatible /audio/speech at a custom URL,
                          native xAI /v1/tts when that URL points at xAI, or
                          OpenRouter chat completions for GPT Audio models
    Retired ``local`` values are normalized to ``disabled``.
    """

    def __init__(self, cache_dir: str = TTS_CACHE_DIR):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        try:
            self.max_cache_bytes = int(os.getenv("ODYSSEUS_TTS_CACHE_MAX_BYTES", 500 * 1024 * 1024))
        except ValueError:
            self.max_cache_bytes = 500 * 1024 * 1024

    # ── Settings ──

    def _load_settings(self) -> dict:
        from src.settings import load_settings
        saved = load_settings()
        provider = saved.get("tts_provider", "disabled")
        if provider == "local":
            provider = "disabled"
        return {
            "tts_enabled": saved.get("tts_enabled", True),
            "tts_provider": provider,
            "tts_model": saved.get("tts_model", "tts-1"),
            "tts_voice": saved.get("tts_voice", "alloy"),
            "tts_speed": saved.get("tts_speed", "1"),
            "tts_endpoint_url": saved.get("tts_endpoint_url", ""),
            "tts_api_key": saved.get("tts_api_key", ""),
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
        if isinstance(provider, str) and provider.startswith("endpoint:"):
            return True  # assume reachable; errors surface at synthesis time
        if provider in ("custom", "api", "custom_endpoint"):
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

        self._enforce_cache_limit()

    def _enforce_cache_limit(self):
            """Evicts oldest files if the cache exceeds the configured byte limit."""
            if self.max_cache_bytes <= 0:
                return

            try:
                files = []
                total_size = 0

                # Safely scan files and sum sizes, ignoring files deleted mid-scan
                for f in self.cache_dir.iterdir():
                    try:
                        if f.is_file() and f.suffix.lower() in (".mp3", ".wav"):
                            files.append(f)
                            total_size += f.stat().st_size
                    except OSError:
                        continue

                if total_size > self.max_cache_bytes:
                    logger.info(
                        f"TTS cache ({total_size} bytes) exceeded limit ({self.max_cache_bytes} bytes). Evicting oldest files."
                    )

                    # Sort files by modification time (oldest first)
                    try:
                        files.sort(key=lambda f: f.stat().st_mtime)
                    except OSError as e:
                        logger.warning(f"Failed to sort cache files by mtime: {e}")

                    # Trim down to 80% of max capacity
                    target_size = self.max_cache_bytes * 0.8

                    while files and total_size > target_size:
                        f = files.pop(0)
                        try:
                            size = f.stat().st_size
                            f.unlink()
                            total_size -= size
                        except OSError as e:
                            logger.warning(f"Failed to evict cache file {f}: {e}")
                            continue

            except Exception as e:
                logger.warning(f"Error enforcing TTS cache limit: {e}", exc_info=True)

    def clear_cache(self):
        count = 0
        for f in self.cache_dir.glob("*.*"):
            f.unlink()
            count += 1
        logger.info(f"Cleared {count} cached TTS files")

    # ── API endpoint ──

    def _synthesize_openai_compatible(self, url: str, api_key: Optional[str],
                                      text: str, model: str, voice: str,
                                      speed: float = 1.0) -> bytes:
        """POST to an OpenAI-compatible /audio/speech endpoint."""
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
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            detail = ""
            try:
                body = e.response.text.strip()
                if api_key:
                    body = body.replace(api_key, "[redacted]")
                if body:
                    detail = f": {body[:300]}"
            except Exception:
                pass
            logger.error("TTS endpoint returned HTTP %s", status)
            raise TTSError(
                f"TTS endpoint returned HTTP {status}{detail}. "
                "Check that it implements OpenAI-compatible /audio/speech and supports "
                "the selected model/voice."
            ) from e
        except httpx.RequestError as e:
            logger.error("TTS endpoint request failed: %s", e.__class__.__name__)
            raise TTSError(f"Could not reach TTS endpoint: {e.__class__.__name__}.") from e
        except Exception as e:
            logger.error("TTS endpoint synthesis failed: %s", e)
            raise TTSError("TTS endpoint synthesis failed.") from e

        logger.info("API TTS: %s bytes", len(r.content))
        return r.content

    def _synthesize_openrouter_audio(self, base_url: str, api_key: Optional[str],
                                     text: str, model: str, voice: str) -> bytes:
        """Synthesize OpenAI GPT Audio through OpenRouter chat completions.

        OpenRouter audio models are chat models: the request opts into audio
        output with ``modalities`` and an ``audio`` config, and OpenRouter
        requires ``stream: true`` for audio output. Streaming only supports
        ``pcm16``, so audio arrives as SSE ``data:`` events carrying base64
        raw PCM16 chunks in ``choices[0].delta.audio.data`` (with an optional
        final ``choices[0].message.audio.data``). The accumulated samples are
        wrapped in a WAV container for browser playback/caching.
        """
        import base64
        import json

        url = _chat_completions_url(base_url)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model,
            "modalities": ["text", "audio"],
            "audio": {"voice": voice or "alloy", "format": "pcm16"},
            "messages": [{"role": "user", "content": text}],
            "stream": True,
        }

        def _redact(value: str) -> str:
            return value.replace(api_key, "[redacted]") if api_key and value else value

        def _provider_detail(response) -> str:
            try:
                response.read()
                body = (response.text or "").strip()
            except Exception:
                body = ""
            body = _redact(body)
            return f": {body[:300]}" if body else ""

        parts = []
        pending_b64 = ""
        saw_audio = False

        def _decode_b64(value: str) -> None:
            try:
                parts.append(base64.b64decode(value, validate=True))
            except Exception as e:
                raise TTSError(
                    "OpenRouter GPT Audio returned invalid base64 audio."
                ) from e

        def _feed_audio(value) -> None:
            nonlocal pending_b64, saw_audio
            if not isinstance(value, str) or not value.strip():
                return
            chunk = value.strip()
            if chunk.startswith("data:") and "," in chunk:
                chunk = chunk.split(",", 1)[1]
            chunk = "".join(chunk.split())
            if not chunk:
                return
            saw_audio = True

            # A lone trailing "=" is ambiguous: it may end an independently
            # encoded chunk, or be the first half of "==" padding split across
            # SSE deltas. A following non-"==" chunk resolves that; a
            # following "=" completes the split padding.
            if (pending_b64.endswith("=")
                    and not pending_b64.endswith("==")
                    and not chunk.startswith("=")):
                _decode_b64(pending_b64)
                pending_b64 = ""

            pending_b64 += chunk

            # Decode complete base64 groups as they arrive. This handles both
            # independently encoded chunks (including padding) and a single
            # base64 stream split across SSE deltas.
            if pending_b64.endswith("=="):
                _decode_b64(pending_b64)
                pending_b64 = ""
                return
            if pending_b64.endswith("="):
                return
            complete = len(pending_b64) - (len(pending_b64) % 4)
            if complete >= 4:
                segment, pending_b64 = pending_b64[:complete], pending_b64[complete:]
                _decode_b64(segment)

        try:
            with httpx.stream("POST", url, json=payload, headers=headers,
                              timeout=60) as r:
                if not (200 <= r.status_code < 300):
                    detail = _provider_detail(r)
                    logger.error("OpenRouter audio endpoint returned HTTP %s", r.status_code)
                    raise TTSError(
                        f"OpenRouter audio endpoint returned HTTP {r.status_code}{detail}. "
                        "Check that the endpoint supports GPT Audio and the selected voice."
                    )

                for raw_line in r.iter_lines():
                    line = raw_line.decode("utf-8", "ignore") if isinstance(raw_line, bytes) else raw_line
                    line = (line or "").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        event = json.loads(data)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("error"):
                        error = event.get("error")
                        if isinstance(error, dict):
                            error = error.get("message") or error
                        logger.error("OpenRouter GPT Audio stream returned an error")
                        raise TTSError(
                            f"OpenRouter audio endpoint returned an error: {_redact(str(error))[:300]}"
                        )
                    choices = event.get("choices") or []
                    if not choices or not isinstance(choices[0], dict):
                        continue
                    choice = choices[0]
                    for container in ("delta", "message"):
                        block = choice.get(container)
                        if not isinstance(block, dict):
                            continue
                        audio = block.get("audio")
                        if isinstance(audio, dict):
                            _feed_audio(audio.get("data"))

            if pending_b64:
                padded = pending_b64 + "=" * (-len(pending_b64) % 4)
                try:
                    parts.append(base64.b64decode(padded, validate=True))
                except Exception as e:
                    raise TTSError(
                        "OpenRouter GPT Audio returned invalid base64 audio."
                    ) from e
        except TTSError:
            raise
        except httpx.RequestError as e:
            logger.error("OpenRouter audio endpoint request failed: %s", e.__class__.__name__)
            raise TTSError(f"Could not reach OpenRouter audio endpoint: {e.__class__.__name__}.") from e
        except Exception as e:
            logger.error("OpenRouter audio synthesis failed: %s", e)
            raise TTSError("OpenRouter audio synthesis failed.") from e

        if not saw_audio:
            raise TTSError(
                "OpenRouter GPT Audio response did not include audio data. "
                "Check that the selected model supports audio output."
            )
        pcm = b"".join(parts)
        if not pcm:
            raise TTSError("OpenRouter GPT Audio returned empty audio data.")

        if pcm[:4] == b"RIFF" and pcm[8:12] == b"WAVE":
            audio = pcm
        else:
            audio = _pcm16_to_wav(pcm)
        logger.info(
            "OpenRouter GPT Audio: %s PCM bytes -> %s WAV bytes",
            len(pcm),
            len(audio),
        )
        return audio

    def _synthesize_xai(self, base_url: str, api_key: Optional[str],
                        text: str, voice: str, speed: float = 1.0) -> bytes:
        """POST to xAI's native /v1/tts endpoint.

        xAI uses ``voice_id``/``output_format`` rather than the
        OpenAI-compatible ``voice``/``response_format`` shape, and returns raw
        audio bytes.
        """
        url = _xai_tts_url(base_url)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "text": text,
            "voice_id": voice or "eve",
            "language": "en",
            "output_format": {"codec": "mp3"},
            "speed": _xai_speed(speed),
        }

        try:
            r = httpx.post(url, json=payload, headers=headers, timeout=60)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            detail = ""
            try:
                body = e.response.text.strip()
                if api_key:
                    body = body.replace(api_key, "[redacted]")
                if body:
                    detail = f": {body[:300]}"
            except Exception:
                pass
            logger.error("xAI TTS endpoint returned HTTP %s", status)
            raise TTSError(
                f"xAI TTS endpoint returned HTTP {status}{detail}. "
                "Check the API key and selected voice_id; native xAI TTS uses "
                "POST /v1/tts."
            ) from e
        except httpx.RequestError as e:
            logger.error("xAI TTS endpoint request failed: %s", e.__class__.__name__)
            raise TTSError(f"Could not reach xAI TTS endpoint: {e.__class__.__name__}.") from e
        except Exception as e:
            logger.error("xAI TTS endpoint synthesis failed: %s", e)
            raise TTSError("xAI TTS endpoint synthesis failed.") from e

        logger.info("xAI TTS: %s bytes", len(r.content))
        return r.content

    def _synthesize_api(self, text: str, endpoint_id: str, model: str, voice: str, speed: float = 1.0) -> bytes:
        from src.database import SessionLocal, ModelEndpoint

        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == endpoint_id).first()
            if not ep:
                raise TTSError(f"Configured TTS endpoint '{endpoint_id}' was not found.")
            base_url = ep.base_url
            api_key = ep.api_key
            endpoint_name = ep.name
        finally:
            db.close()

        if _is_openrouter_audio_endpoint(base_url, model):
            return self._synthesize_openrouter_audio(base_url, api_key, text, model, voice)
        if _is_xai_endpoint(base_url, endpoint_name, model):
            return self._synthesize_xai(base_url, api_key, text, voice, speed)
        return self._synthesize_openai_compatible(
            _speech_url(base_url), api_key, text, model, voice, speed
        )

    def _synthesize_custom(self, text: str, url: str, api_key: Optional[str],
                           model: str, voice: str, speed: float = 1.0) -> bytes:
        if _is_openrouter_audio_endpoint(url, model):
            return self._synthesize_openrouter_audio(url, api_key, text, model, voice)
        if _is_xai_endpoint(url, model):
            return self._synthesize_xai(url, api_key, text, voice, speed)
        return self._synthesize_openai_compatible(
            _speech_url(url), api_key, text, model, voice, speed
        )

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
        if not isinstance(provider, str):
            logger.error("Unknown TTS provider: %r", provider)
            return None

        if len(text) > 5000:
            text = text[:5000]

        cache_provider = provider
        if provider in ("custom", "api", "custom_endpoint"):
            cache_provider = f"custom:{settings.get('tts_endpoint_url', '')}"
        elif isinstance(provider, str) and provider.startswith("endpoint:"):
            cache_provider = provider

        if use_cache:
            key = self._cache_key(text, cache_provider, model, voice, speed)
            cached = self._get_cached(key)
            if cached:
                logger.info(f"TTS cache hit ({len(text)} chars)")
                return cached

        audio_data = None

        if provider.startswith("endpoint:"):
            endpoint_id = provider.split(":", 1)[1]
            audio_data = self._synthesize_api(text, endpoint_id, model, voice, speed)
        elif provider in ("custom", "api", "custom_endpoint"):
            audio_data = self._synthesize_custom(
                text,
                settings.get("tts_endpoint_url", ""),
                settings.get("tts_api_key", ""),
                model,
                voice,
                speed,
            )
        else:
            logger.error(f"Unknown TTS provider: {provider}")
            return None

        if audio_data and use_cache:
            key = self._cache_key(text, cache_provider, model, voice, speed)
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

        if provider == "browser":
            stats["model"] = "Browser (Web Speech API)"
        elif isinstance(provider, str) and provider.startswith("endpoint:"):
            stats["endpoint_id"] = provider.split(":", 1)[1]
        elif provider in ("custom", "api", "custom_endpoint"):
            stats["endpoint_url"] = settings.get("tts_endpoint_url", "")
            stats["has_api_key"] = bool(settings.get("tts_api_key"))

        return stats


# Module-level singleton
_tts_service = None

def get_tts_service() -> TTSService:
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service
