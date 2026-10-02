# services/stt/stt_service.py
"""Multi-provider Speech-to-Text service — dispatches to local Whisper, OpenAI-compatible API, Gemini native API, or browser."""

import gc
import io
import logging
import httpx
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _first_text_field(item: Any) -> Any:
    """First non-empty string under a known transcript-text key, else "".

    Documented Interactions outputs carry `text`; `transcript` /
    `transcription` are tolerated so a schema variation degrades to a
    working transcript instead of an empty result.
    """
    if not isinstance(item, dict):
        return ""
    for key in STTService._GEMINI_OUTPUT_TEXT_KEYS:
        value = item.get(key, "")
        if isinstance(value, str) and value:
            return value
    return ""

# Phase 1 allowlists. Audio formats accepted for local STT transcription.
# This mirrors UploadHandler.is_audio_file extensions; the temp-file suffix is
# chosen from the original filename so the faster-whisper decoder sees a
# familiar container. Anything else falls back to .webm (mic path).
STT_AUDIO_SUFFIXES = (
    ".webm", ".weba", ".wav", ".mp3", ".m4a",
    ".ogg", ".oga", ".opus", ".flac", ".aac", ".aiff", ".aif",
)
# Conservative faster-whisper model id: bare sizes plus HF-style repo paths.
# Validated to avoid path traversal / shell metachars crashing the loader.
_STT_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,127}$")
# Whisper language codes: empty = auto-detect, else short code like en/sk/cs.
_STT_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z]{2,8})?$")


class STTService:
    """Multi-provider STT service.

    Reads provider config from data/settings.json on each call.
    Providers:
      "disabled"        — no STT
      "browser"         — client-side Web Speech API (no server transcription)
      "local"           — faster-whisper on CPU/GPU (lazy-loaded, see below)
      "endpoint:<id>"   — OpenAI-compatible /audio/transcriptions, or the
                          native Gemini transcription API when the endpoint
                          is a generativelanguage.googleapis.com URL.

    Local model lifecycle (lazy by design — important on low-RAM hosts such
    as Raspberry Pi, where a resident medium/large model exhausts memory):
      - The model is NEVER loaded at startup, on provider/model selection,
        or from read-only paths (available / get_stats).
      - It loads only inside an actual transcription request.
      - keep_model_loaded=false (default): released right after each
        transcription. keep_model_loaded=true: kept for reuse.
      - A stt_model change only invalidates the cache; the replacement
        loads on the next real transcription request.
      - Note: dropping our reference frees the Python objects, but OS
        allocator behavior (arena retention) may mean process RSS does not
        instantly return to baseline. No process termination is used.
    """

    def __init__(self):
        self._whisper_model = None  # lazy-init
        self._loaded_model_size = None  # tracks which stt_model is cached
        # Serializes lazy model loads so concurrent transcription requests
        # cannot race and instantiate/download the same model twice.
        self._model_lock = threading.Lock()

    def invalidate_model(self) -> None:
        """Drop the cached Whisper model so the next call reloads stt_model.

        Invalidation only — it never loads the replacement. Called
        implicitly on size change inside transcription paths; also available
        for explicit invalidation after settings writes.
        """
        with self._model_lock:
            self._whisper_model = None
            self._loaded_model_size = None

    def is_model_loaded(self) -> bool:
        """True if a Whisper model is currently resident. Never loads."""
        with self._model_lock:
            return self._whisper_model is not None

    # ── Settings ──

    def _load_settings(self) -> dict:
        from src.settings import load_settings
        saved = load_settings()
        return {
            "stt_enabled": saved.get("stt_enabled", False),
            "stt_provider": saved.get("stt_provider", "disabled"),
            "stt_model": saved.get("stt_model", "base"),
            "stt_language": saved.get("stt_language", ""),
            "keep_model_loaded": saved.get("keep_model_loaded", False),
        }

    @staticmethod
    def _is_faster_whisper_importable() -> bool:
        """True if the faster-whisper package is installed. Never loads a model."""
        import importlib.util
        try:
            return importlib.util.find_spec("faster_whisper") is not None
        except Exception:
            return False

    @staticmethod
    def is_google_base_url(base_url: Any) -> bool:
        """True for Google Gemini API bases (native transcription path).

        Mirrors routes/model_routes._is_google_api_base (kept local to avoid
        a routes import cycle from the service layer).
        """
        try:
            return (urlparse(str(base_url or "")).hostname or "").lower() == (
                "generativelanguage.googleapis.com"
            )
        except Exception:
            return False

    @property
    def available(self) -> bool:
        # Read-only: must NOT load the Whisper model (lazy lifecycle).
        settings = self._load_settings()
        if settings.get("stt_enabled") is False:
            return False
        provider = settings["stt_provider"]
        if provider == "disabled":
            return False
        if provider == "browser":
            return True  # handled client-side
        if provider == "local":
            # Installed == usable; the model itself loads on first real
            # transcription request, never here.
            return self._is_faster_whisper_importable()
        if provider.startswith("endpoint:"):
            return True  # assume reachable
        return False

    @staticmethod
    def sanitize_model_size(value: Any) -> Optional[str]:
        """Return a safe faster-whisper model id, or None if malformed."""
        if not isinstance(value, str):
            return None
        v = value.strip()
        if not v or len(v) > 128:
            return None
        if ".." in v or v.startswith(("/", ".")):
            return None
        if not _STT_MODEL_RE.match(v):
            return None
        return v

    @staticmethod
    def sanitize_language(value: Any) -> Optional[str]:
        """Return normalized language code ("" = auto), or None if malformed."""
        if value is None:
            return ""
        if not isinstance(value, str):
            return None
        v = value.strip().lower()
        if v == "":
            return ""
        if len(v) > 16:
            return None
        if not _STT_LANGUAGE_RE.match(v):
            return None
        return v

    @staticmethod
    def suffix_for_filename(filename: Any) -> str:
        """Pick a safe temp-file suffix for an uploaded audio filename."""
        if isinstance(filename, str):
            low = filename.lower()
            for ext in STT_AUDIO_SUFFIXES:
                if low.endswith(ext):
                    return ext
        return ".webm"

    # ── Local Whisper ──

    def _get_whisper(self):
        """Return the cached model, loading it on demand (transcription paths only).

        Callers other than actual transcription (available, get_stats, UI
        prefetch) must use is_model_loaded() instead — loading here on
        selection/stats access is exactly what the lazy lifecycle forbids.
        The lock guarantees a single instantiation under concurrency; the
        second waiter reuses the winner's model.
        """
        with self._model_lock:
            try:
                _current_size = self._load_settings().get("stt_model", "base")
            except Exception:
                _current_size = None
            if (
                self._whisper_model is not None
                and _current_size is not None
                and _current_size != self._loaded_model_size
            ):
                # Config changed: invalidate only. The replacement loads
                # below because THIS call is a real transcription request.
                logger.info(
                    f"STT model changed '{self._loaded_model_size}' -> '{_current_size}', reloading on demand"
                )
                self._whisper_model = None
                self._loaded_model_size = None
            if self._whisper_model is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError:
                    logger.warning("faster-whisper not installed. Install with: pip install faster-whisper")
                    return None
                try:
                    settings = self._load_settings()
                    raw_size = settings.get("stt_model", "base")
                    model_size = self.sanitize_model_size(raw_size)
                    if model_size is None:
                        logger.error(f"Invalid stt_model setting rejected: {raw_size!r}")
                        return None
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
                    # Observable lifecycle: log when loading starts (this can
                    # download weights + take a while on first use) and when
                    # the model is actually resident, with elapsed time.
                    logger.info(
                        f"Loading faster-whisper model '{model_size}' "
                        f"(device={device}, compute_type={compute_type})..."
                    )
                    load_start = time.monotonic()
                    self._whisper_model = WhisperModel(model_size, device=device, compute_type=compute_type)
                    self._loaded_model_size = model_size
                    logger.info(
                        f"faster-whisper model '{model_size}' loaded on {device} "
                        f"in {time.monotonic() - load_start:.1f}s"
                    )
                except Exception as e:
                    logger.error(f"Failed to load whisper model: {e}")
                    return None
            return self._whisper_model

    def _maybe_unload(self) -> None:
        """Release the Whisper model unless keep_model_loaded is set.

        Called after each local transcription. Clearing the reference drops
        the CTranslate2 model object; gc.collect() reclaims the Python side
        promptly. OS RSS may lag (allocator arenas) — documented, not a leak.
        Safe under concurrency: an in-flight transcription holds its own
        local reference, so unloading only affects future requests.
        """
        try:
            keep = bool(self._load_settings().get("keep_model_loaded", False))
        except Exception:
            keep = False
        if keep:
            return
        with self._model_lock:
            had_model = self._whisper_model is not None
            released_size = self._loaded_model_size
            self._whisper_model = None
            self._loaded_model_size = None
        if had_model:
            logger.info(f"Released faster-whisper model '{released_size}' from memory")
        gc.collect()

    def _transcribe_local(
        self, audio_bytes: bytes, language: str = "", filename_hint: str = ""
    ) -> Optional[str]:
        """Transcribe locally, returning text only (back-compat wrapper)."""
        result = self._transcribe_local_with_info(audio_bytes, language, filename_hint)
        if result is None:
            return None
        return result[0]

    def _transcribe_local_with_info(
        self, audio_bytes: bytes, language: str = "", filename_hint: str = ""
    ) -> Optional[Tuple[str, str]]:
        """Transcribe locally, returning (text, detected_language)."""
        model = self._get_whisper()
        if not model:
            return None
        tmp_path = None
        try:
            # Write to temp file (faster-whisper needs a file path or file-like).
            # Suffix follows the original container so the decoder sees a
            # familiar format; mic blobs default to .webm.
            suffix = self.suffix_for_filename(filename_hint)
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            safe_lang = self.sanitize_language(language)
            if safe_lang is None:
                logger.warning(f"Ignoring malformed STT language: {language!r}, auto-detecting")
                safe_lang = ""
            kwargs = {}
            if safe_lang:
                kwargs["language"] = safe_lang

            segments, info = model.transcribe(tmp_path, **kwargs)
            # Consume the segment generator with REAL progress counting: log
            # every 25 segments with the audio timestamp reached. This is
            # observed decoding progress (segment end-times), not a percentage
            # estimate — faster-whisper exposes no total-work signal.
            transcribe_start = time.monotonic()
            logger.info(
                f"Local STT started: {len(audio_bytes)} bytes "
                f"({suffix or 'unknown container'}), model="
                f"'{self._loaded_model_size}', language={safe_lang or 'auto'}"
            )
            texts = []
            seg_count = 0
            last_end = 0.0
            for seg in segments:
                seg_text = getattr(seg, "text", "") or ""
                texts.append(seg_text.strip() if isinstance(seg_text, str) else "")
                seg_count += 1
                try:
                    last_end = float(getattr(seg, "end", 0.0) or 0.0)
                except (TypeError, ValueError):
                    pass
                if seg_count % 25 == 0:
                    logger.info(
                        f"Local STT progress: {seg_count} segments, "
                        f"audio reached {last_end:.1f}s "
                        f"({time.monotonic() - transcribe_start:.0f}s elapsed)"
                    )
            text = " ".join(texts)
            detected = getattr(info, "language", "") or ""
            audio_duration = getattr(info, "duration", 0) or 0

            logger.info(
                f"Local STT done: {len(text)} chars, {seg_count} segments, "
                f"audio {audio_duration:.1f}s in {time.monotonic() - transcribe_start:.1f}s, "
                f"lang={detected}, prob={getattr(info, 'language_probability', 0):.2f}"
            )
            return text, detected
        except Exception as e:
            logger.error(f"Local STT transcription failed: {e}", exc_info=True)
            return None
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

    # ── API endpoint ──

    _API_MIME_FOR_SUFFIX = {
        ".webm": "audio/webm",
        ".weba": "audio/webm",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".oga": "audio/ogg",
        ".opus": "audio/opus",
        ".flac": "audio/flac",
        ".aac": "audio/aac",
        ".aiff": "audio/aiff",
        ".aif": "audio/aiff",
    }

    def _transcribe_api(self, audio_bytes: bytes, endpoint_id: str, model: str, language: str = "", filename_hint: str = "") -> Optional[str]:
        result = self._transcribe_api_with_info(audio_bytes, endpoint_id, model, language, filename_hint)
        if result is None:
            return None
        return result[0]

    def _transcribe_api_with_info(self, audio_bytes: bytes, endpoint_id: str, model: str, language: str = "", filename_hint: str = "") -> Optional[Tuple[str, str]]:
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

        # Google endpoints speak the native Gemini API, not the
        # OpenAI-compatible /audio/transcriptions shim (which 404s there).
        if self.is_google_base_url(base_url):
            return self._transcribe_gemini_with_info(
                audio_bytes, base_url, api_key, model, language, filename_hint
            )

        url = base_url + "/audio/transcriptions"
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        suffix = self.suffix_for_filename(filename_hint)
        mime = self._API_MIME_FOR_SUFFIX.get(suffix, "audio/webm")
        remote_name = f"audio{suffix}"
        files = {"file": (remote_name, io.BytesIO(audio_bytes), mime)}
        data = {"model": model or "whisper-1"}
        safe_lang = self.sanitize_language(language)
        if safe_lang is None:
            logger.warning(f"Ignoring malformed STT language: {language!r}")
            safe_lang = ""
        if safe_lang:
            data["language"] = safe_lang

        try:
            r = httpx.post(url, headers=headers, files=files, data=data, timeout=60)
            r.raise_for_status()
            result = r.json()
            text = result.get("text", "")
            # OpenAI-compatible responses rarely include detected language;
            # preserve the requested value so callers have a stable field.
            logger.info(f"API STT: {len(text)} chars from {base_url}")
            return text, safe_lang
        except Exception as e:
            logger.error(f"API STT transcription failed: {e}")
            return None

    # ── Gemini native transcription ──

    #: Default when the endpoint leaves the model blank. The UI lets the user
    #: pick any transcription model; this is only the fallback.
    GEMINI_DEFAULT_MODEL = "gemini-3.5-transcribe"
    #: MIME types exactly as documented for gemini-3.5-transcribe
    #: (ai.google.dev/gemini-api/docs/transcribe "Supported audio formats").
    #: NOTE: .m4a is audio/m4a here (the generic OpenAI-compat map uses
    #: audio/mp4); .mp3 is audio/mp3.
    _GEMINI_MIME_FOR_SUFFIX = {
        ".webm": "audio/webm",
        ".wav": "audio/wav",
        ".mp3": "audio/mp3",
        ".m4a": "audio/m4a",
        ".ogg": "audio/ogg",
    }
    #: Output-item keys that may carry transcript text. Documented shape is
    #: {"type": ..., "text": ...}; the alternates are tolerated defensively.
    _GEMINI_OUTPUT_TEXT_KEYS = ("text", "transcript", "transcription")
    #: Poll cadence/budget for Interactions completion (sync worker thread —
    #: routes run transcription via asyncio.to_thread, so sleep is safe).
    GEMINI_POLL_INTERVAL_SECONDS = 5
    GEMINI_POLL_MAX_ATTEMPTS = 48  # ~4 minutes, comparable to slow local runs

    def _gemini_native_root(self, base_url: str) -> str:
        """Strip the OpenAI-compat suffix so native paths can be appended.

        Mirrors routes/model_routes._google_native_root (kept local to avoid
        a routes import cycle from the service layer).
        """
        try:
            parsed = urlparse((base_url or "").rstrip("/"))
        except Exception:
            return "https://generativelanguage.googleapis.com/v1beta"
        path = (parsed.path or "").rstrip("/")
        if path.endswith("/openai"):
            path = path[: -len("/openai")].rstrip("/")
        if not path:
            path = "/v1beta"
        return parsed._replace(path=path, query="", fragment="").geturl().rstrip("/")

    @staticmethod
    def _gemini_error_message(status: int, body: str, model: str) -> str:
        """Map a Gemini HTTP failure to a safe, actionable message (no secrets)."""
        snippet = (body or "")[:200]
        if status in (401, 403):
            return (
                "Gemini rejected the API key (HTTP %s). Check the endpoint's "
                "stored key and the Google AI API being enabled. %s" % (status, snippet)
            )
        if status == 404:
            return (
                "Gemini has no such model or path (HTTP 404). The configured "
                f"model '{model}' may be unavailable in this region/API version. {snippet}"
            )
        if status == 429:
            return f"Gemini rate limit hit (HTTP 429). Retry later. {snippet}"
        if status == 400:
            return (
                "Gemini rejected the request (HTTP 400) — possibly an "
                f"unsupported audio format/size for model '{model}'. {snippet}"
            )
        return f"Gemini transcription failed (HTTP {status}). {snippet}"

    @staticmethod
    def _extract_gemini_transcript(result: Any) -> Tuple[str, str]:
        """Extract transcript text from an Interactions/generateContent JSON response.

        Mirrors the SDK `response.text` semantics: concatenate the `text` of
        every non-thought part across all candidates, verbatim. Returns
        (text, diagnosis); diagnosis is "" on success and describes the
        response shape otherwise (finish reasons, block reason, part kinds),
        so empty-but-200 responses are debuggable instead of opaque.
        """
        texts: list = []
        thought_texts: list = []
        finish_reasons: list = []
        saw_parts_without_text = 0
        part_kinds: set = set()
        candidates = []
        if isinstance(result, dict):
            candidates = result.get("candidates", []) or []
        if not isinstance(candidates, list):
            candidates = []
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            fr = cand.get("finishReason")
            if isinstance(fr, str) and fr:
                finish_reasons.append(fr)
            content = cand.get("content", {}) or {}
            # Content is normally {"role": ..., "parts": [...]}; tolerate a
            # bare parts list for forward-compatibility.
            parts = content if isinstance(content, list) else content.get("parts", []) or []
            if not isinstance(parts, list):
                continue
            for part in parts:
                if not isinstance(part, dict):
                    continue
                for key in part.keys():
                    if key not in ("text", "thought", "thoughtSignature"):
                        part_kinds.add(key)
                t = part.get("text")
                if isinstance(t, str) and t:
                    if part.get("thought") is True:
                        thought_texts.append(t)
                    else:
                        texts.append(t)
                else:
                    saw_parts_without_text += 1
        if texts:
            return "".join(texts), ""
        block_reason = ""
        if isinstance(result, dict):
            feedback = result.get("promptFeedback", {}) or {}
            if isinstance(feedback, dict):
                block_reason = feedback.get("blockReason", "") or ""
        detail = []
        if block_reason:
            detail.append(f"blockReason={block_reason}")
        if finish_reasons:
            detail.append("finishReasons=" + ",".join(finish_reasons))
        if thought_texts and not texts:
            detail.append(f"{len(thought_texts)} thought-only part(s), no transcript text")
        if saw_parts_without_text and not part_kinds:
            detail.append(f"{saw_parts_without_text} text-less part(s)")
        if part_kinds:
            detail.append("part keys=" + ",".join(sorted(part_kinds)))
        if not detail:
            keys = sorted(result.keys()) if isinstance(result, dict) else []
            detail.append(f"no candidates/parts (top-level keys: {keys})")
        return "", "; ".join(detail)

    @staticmethod
    def _gemini_response_structure(result: Any) -> str:
        """One-line sanitized sketch of an Interactions/generateContent response for logs.

        Structure only (keys, status, output/item types, part kinds, text
        lengths, finish reasons) — never transcript content, never API keys,
        never audio bytes.
        """
        if not isinstance(result, dict):
            return f"non-dict response ({type(result).__name__})"
        bits = [f"keys={sorted(result.keys())}"]
        if "status" in result:
            bits.append(f"status={result.get('status', '?')}")
        steps = result.get("steps", None)
        if steps is not None:
            if isinstance(steps, list):
                kinds = []
                for item in steps[-4:]:
                    if not isinstance(item, dict):
                        kinds.append(type(item).__name__)
                        continue
                    # Include finishReason for debugging
                    fr = item.get("finishReason")
                    if isinstance(fr, str) and fr:
                        bits.append(f"finish={fr}")
                    content = item.get("content", [])
                    if isinstance(content, list):
                        for part in content:
                            if not isinstance(part, dict):
                                kinds.append(type(part).__name__)
                                continue
                            t = part.get("text", "")
                            kinds.append(
                                f"{part.get('type', '?')}:text[{len(t)}]"
                                if isinstance(t, str) and t
                                else str(part.get("type", type(part).__name__))
                            )
                bits.append(f"steps={len(steps)} [{','.join(kinds)}]")
            else:
                bits.append(f"steps={type(steps).__name__}")
        outputs = result.get("outputs", None)
        feedback = result.get("promptFeedback", {})
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            bits.append(f"blockReason={feedback['blockReason']}")
        return "; ".join(bits)

    def _gemini_upload_file(
        self, origin: str, api_key: Any, audio_bytes: bytes, mime: str, display_name: str
    ) -> Optional[str]:
        """Upload audio bytes via the Files API resumable protocol.

        Documented flow (ai.google.dev/gemini-api/docs/transcribe): start an
        upload session, send the bytes with upload+finalize, read back the
        file URI for use as generateContent `file_data`. Returns the file URI
        or None. Inline `inline_data` is deliberately NOT used here: it caps
        total request size at 20 MB (below our 25 MB STT cap) and the
        dedicated transcribe model documents the Files API transport.
        """
        start_url = f"{origin}/upload/v1beta/files"
        start_headers = {
            "x-goog-api-key": str(api_key or ""),
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(len(audio_bytes)),
            "X-Goog-Upload-Header-Content-Type": mime,
            "Content-Type": "application/json",
        }
        try:
            start = httpx.post(
                start_url,
                headers=start_headers,
                json={"file": {"display_name": display_name or "odysseus-audio"}},
                timeout=60,
            )
            if start.status_code != 200:
                logger.error(
                    "Gemini Files API upload session failed: "
                    + self._gemini_error_message(start.status_code, start.text, "Files API upload")
                )
                return None
            # httpx Headers is case-insensitive; tolerate plain-dict fakes.
            get_header = getattr(start.headers, "get", None)
            session_url = (
                get_header("x-goog-upload-url")
                if callable(get_header)
                else dict(start.headers).get("x-goog-upload-url")
            )
            if not session_url:
                logger.error("Gemini Files API upload session returned no upload URL")
                return None
            finalize = httpx.post(
                session_url,
                headers={
                    "Content-Length": str(len(audio_bytes)),
                    "X-Goog-Upload-Offset": "0",
                    "X-Goog-Upload-Command": "upload, finalize",
                },
                content=audio_bytes,
                timeout=180,
            )
            if finalize.status_code != 200:
                logger.error(
                    "Gemini Files API upload failed: "
                    + self._gemini_error_message(finalize.status_code, finalize.text, "Files API upload")
                )
                return None
            try:
                uploaded = finalize.json()
            except Exception as e:
                logger.error(f"Gemini Files API upload returned non-JSON: {e}")
                return None
            file_info = (uploaded or {}).get("file", {}) if isinstance(uploaded, dict) else {}
            uri = file_info.get("uri", "") if isinstance(file_info, dict) else ""
            if not uri:
                logger.error("Gemini Files API upload response has no file URI")
                return None
            return uri
        except Exception as e:
            logger.error(f"Gemini Files API upload failed: {e}")
            return None

    @staticmethod
    def _extract_interaction_text(result: Any) -> Tuple[str, str]:
        """Extract transcript from an Interactions API response object.

        Prefers `output_text` (the SDK-visible completed transcript), else
        the `steps[].content[].text` structure (Interactions API documented
        completion shape), else the last text-bearing entry of `outputs`
        (documented completion shape: `outputs[-1].text`). Returns (text,
        diagnosis).
        """
        if not isinstance(result, dict):
            return "", f"non-dict response ({type(result).__name__})"
        raw_output = result.get("output_text", "")
        if isinstance(raw_output, str) and raw_output:
            return raw_output, ""
        # New Interactions API completion shape: steps[].content[].text
        steps = result.get("steps", [])
        if isinstance(steps, list) and steps:
            texts = []
            for step in steps:
                if not isinstance(step, dict):
                    continue
                content = step.get("content", [])
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict):
                            item_type = item.get("type", "")
                            if item_type == "text":
                                t = item.get("text", "")
                                if isinstance(t, str) and t:
                                    texts.append(t)
            if texts:
                return "".join(texts), ""
        outputs = result.get("outputs", [])
        if isinstance(outputs, list) and outputs:
            kinds = []
            for item in outputs:
                if not isinstance(item, dict):
                    kinds.append(type(item).__name__)
                    continue
                t = item.get("text", "")
                kinds.append(
                    f"{item.get('type', '?')}:text[{len(t)}]"
                    if isinstance(t, str) and t
                    else str(item.get("type", "non-dict"))
                )
            # SDK output_text semantics: join trailing consecutive text
            # blocks; stop at the first non-text item from the end.
            trailing = []
            for item in reversed(outputs):
                if not isinstance(item, dict):
                    break
                t = item.get("text", "")
                if not isinstance(t, str) or not t:
                    break
                trailing.append(t)
            if trailing:
                return "".join(reversed(trailing)), ""
            return "", f"outputs=[{','.join(kinds)}], no text-bearing output"
        # Legacy generateContent compat: candidates[].content.parts[].text
        candidates = result.get("candidates", [])
        if isinstance(candidates, list) and candidates:
            texts = []
            for cand in candidates:
                if not isinstance(cand, dict):
                    continue
                content = cand.get("content", {}) or {}
                parts = content if isinstance(content, list) else content.get("parts", []) or []
                if not isinstance(parts, list):
                    continue
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    t = part.get("text")
                    if isinstance(t, str) and t:
                        if part.get("thought") is True:
                            continue
                        texts.append(t)
            if texts:
                return "".join(texts), ""
            # Also check for finishReason in candidates for diagnostics
            finish_reasons = []
            for cand in candidates:
                if isinstance(cand, dict):
                    fr = cand.get("finishReason")
                    if isinstance(fr, str) and fr:
                        finish_reasons.append(fr)
            if finish_reasons:
                return "", f"finishReasons={','.join(finish_reasons)} (legacy candidates format)"
        status = result.get("status", "")
        return "", f"status={status or '?'} (no output_text/outputs/steps/candidates yet)"

    @staticmethod
    def _interaction_status(result: Any) -> str:
        """Lowercased interaction status, or "" when absent."""
        if isinstance(result, dict):
            status = result.get("status", "")
            if isinstance(status, str):
                return status.lower()
        return ""

    def _poll_gemini_interaction(
        self, root: str, headers: Dict[str, str], interaction_id: str, gemini_model: str
    ) -> Optional[Tuple[str, str]]:
        """Poll GET {root}/interactions/{id} until completed/failed/timeout.

        Documented completion flow (ai.google.dev/gemini-api/docs/interactions):
        the create call may return only the initial interaction object
        (status queued/in-progress, no outputs). Polling resolves the final
        transcript without any new architecture — same worker thread.
        """
        poll_url = f"{root}/interactions/{interaction_id}"
        last_status = ""
        for _ in range(self.GEMINI_POLL_MAX_ATTEMPTS):
            time.sleep(self.GEMINI_POLL_INTERVAL_SECONDS)
            try:
                pr = httpx.get(poll_url, headers=headers, timeout=60)
            except Exception as e:
                logger.error(f"Gemini interaction poll failed: {e}")
                return None
            if pr.status_code == 429:
                # Rate-limited mid-poll: back off and keep waiting instead of
                # dropping an in-flight transcription.
                logger.warning("Gemini interaction poll rate-limited (HTTP 429); backing off")
                continue
            if pr.status_code != 200:
                logger.error(
                    "Gemini interaction poll failed: "
                    + self._gemini_error_message(pr.status_code, pr.text, gemini_model)
                )
                return None
            try:
                polled = pr.json()
            except Exception as e:
                logger.error(f"Gemini interaction poll returned non-JSON: {e}")
                return None
            last_status = self._interaction_status(polled)
            logger.debug(
                f"Gemini interaction {interaction_id} status: {last_status or '?'} "
                f"({self._gemini_response_structure(polled)})"
            )
            if last_status == "completed":
                text, diagnosis = self._extract_interaction_text(polled)
                if not text:
                    # Tolerate generateContent-shaped bodies on completion.
                    text, diagnosis = self._extract_gemini_transcript(polled)
                if not text:
                    logger.error(
                        "Gemini transcription empty on completion "
                        f"({diagnosis}; structure: {self._gemini_response_structure(polled)})"
                    )
                    return None
                logger.info(f"Gemini STT: {len(text)} chars via {gemini_model} (polled)")
                return text, ""
            if last_status in ("failed", "cancelled"):
                logger.error(
                    "Gemini transcription interaction "
                    f"{last_status} (structure: {self._gemini_response_structure(polled)})"
                )
                return None
            if last_status == "requires_action":
                # Documented terminal-ish state needing user input; polling
                # further cannot complete it — fail fast with the structure.
                logger.error(
                    "Gemini transcription interaction requires action "
                    f"(structure: {self._gemini_response_structure(polled)})"
                )
                return None
            # Any other status (queued / in_progress / unknown): keep polling.
        logger.error(
            f"Gemini transcription timed out waiting for interaction {interaction_id} "
            f"(last status: {last_status or '?'})"
        )
        return None

    def _transcribe_gemini_with_info(
        self,
        audio_bytes: bytes,
        base_url: str,
        api_key: Any,
        model: str,
        language: str = "",
        filename_hint: str = "",
    ) -> Optional[Tuple[str, str]]:
        """Transcribe via the Gemini Interactions API with Files API audio.

        Documented lifecycle (ai.google.dev/gemini-api/docs/transcribe and
        /interactions): Files API resumable upload, then POST {root}/
        interactions with `{model, input: [{type: audio, uri, mime_type}]}`.
        The create call may return only the initial interaction object
        (status queued/in-progress, no outputs) — in that case poll
        GET {root}/interactions/{id} until completed and read the transcript
        from `output_text`/`outputs[-1].text`. The legacy generateContent
        path is NOT used. Raw transcript text is preserved exactly; the
        response exposes no reliable detected-language field, so the
        requested language (or "") is returned.
        """
        safe_lang = self.sanitize_language(language)
        if safe_lang is None:
            logger.warning(f"Ignoring malformed STT language: {language!r}")
            safe_lang = ""
        gemini_model = (model or "").strip() or self.GEMINI_DEFAULT_MODEL
        suffix = self.suffix_for_filename(filename_hint)
        mime = self._GEMINI_MIME_FOR_SUFFIX.get(suffix, "audio/webm")
        root = self._gemini_native_root(base_url)
        try:
            origin = f"{urlparse(root).scheme}://{urlparse(root).hostname}"
        except Exception:
            origin = "https://generativelanguage.googleapis.com"
        display_name = (filename_hint or "").strip() or f"odysseus-audio{suffix}"

        file_uri = self._gemini_upload_file(origin, api_key, audio_bytes, mime, display_name)
        if not file_uri:
            return None

        url = f"{root}/interactions"

        # The dedicated transcribe model documents audio-only input (no text
        # part); other Gemini models get a text instruction alongside the
        # audio so they transcribe instead of describing the clip.
        audio_input = {"type": "audio", "uri": file_uri, "mime_type": mime}
        is_transcribe_model = "transcribe" in gemini_model.lower()
        if is_transcribe_model:
            audio_input_list = [audio_input]
        else:
            instruction = (
                "Transcribe the speech in this audio verbatim. "
                "Output only the transcription: no commentary, no formatting, "
                "no timestamps, no speaker labels."
            )
            if safe_lang:
                instruction += f" The spoken language is '{safe_lang}'."
            audio_input_list = [{"type": "text", "text": instruction}, audio_input]
        payload = {"model": gemini_model, "input": audio_input_list}
        if safe_lang:
            # Documented transcription hint (BCP-47 codes); omitted entirely
            # for auto-detect, which the docs define as omit-or-empty-list.
            payload["generation_config"] = {
                "transcription_config": {"language_codes": [safe_lang]}
            }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["x-goog-api-key"] = str(api_key)

        try:
            r = httpx.post(url, headers=headers, json=payload, timeout=180)
            if r.status_code != 200:
                logger.error(self._gemini_error_message(r.status_code, r.text, gemini_model))
                return None
            try:
                result = r.json()
            except Exception as e:
                logger.error(f"Gemini transcription returned non-JSON: {e}")
                return None
            text, diagnosis = self._extract_interaction_text(result)
            if not text:
                interaction_id = result.get("id", "") if isinstance(result, dict) else ""
                if interaction_id:
                    # Initial object only — resolve via the documented poll flow
                    # instead of failing on the incomplete first response.
                    logger.info(
                        "Gemini transcription interaction "
                        f"{interaction_id} pending "
                        f"({self._interaction_status(result) or 'no status'}); polling for completion"
                    )
                    polled = self._poll_gemini_interaction(root, headers, interaction_id, gemini_model)
                    if polled is None:
                        return None
                    text, _ = polled
                    logger.info(f"Gemini STT: {len(text)} chars via {gemini_model}")
                    return text, safe_lang
                logger.error(
                    "Gemini transcription empty response "
                    f"({diagnosis}; structure: {self._gemini_response_structure(result)})"
                )
                return None
            logger.info(f"Gemini STT: {len(text)} chars via {gemini_model}")
            return text, safe_lang
        except Exception as e:
            logger.error(f"Gemini STT transcription failed: {e}")
            return None

    # ── Public interface ──

    def transcribe(self, audio_bytes: bytes, filename_hint: str = "") -> Optional[str]:
        """Transcribe audio, returning text only (back-compat)."""
        result = self.transcribe_with_info(audio_bytes, filename_hint)
        if result is None:
            return None
        return result[0]

    def transcribe_with_info(self, audio_bytes: bytes, filename_hint: str = "") -> Optional[Tuple[str, str]]:
        """Transcribe audio, returning (text, language).

        Language is the faster-whisper detected language for the local
        provider, or the requested language for API endpoints. Raw text is
        never post-processed here; LLM cleanup is a separate Phase 2 step.
        """
        settings = self._load_settings()
        if settings.get("stt_enabled") is False:
            return None
        provider = settings["stt_provider"]
        model = settings["stt_model"]
        language = settings.get("stt_language", "")

        if provider in ("disabled", "browser"):
            return None

        if provider == "local":
            result = self._transcribe_local_with_info(audio_bytes, language, filename_hint)
            # Default lifecycle: don't keep large models resident in RAM.
            self._maybe_unload()
            return result
        elif provider.startswith("endpoint:"):
            endpoint_id = provider.split(":", 1)[1]
            return self._transcribe_api_with_info(audio_bytes, endpoint_id, model, language, filename_hint)
        else:
            logger.error(f"Unknown STT provider: {provider}")
            return None

    def get_stats(self) -> Dict[str, Any]:
        # Read-only: must NOT load the Whisper model (lazy lifecycle).
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
            "keep_model_loaded": bool(settings.get("keep_model_loaded", False)),
        }

        if provider == "local":
            stats["model_loaded"] = self.is_model_loaded()
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
