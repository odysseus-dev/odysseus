# routes/stt_routes.py
"""STT API routes — multi-provider (local Whisper, API endpoint, browser)."""

from fastapi import APIRouter, HTTPException, UploadFile, File, Request
import asyncio
import logging
import os

from src.auth_helpers import effective_user
from src.upload_limits import read_upload_limited, STT_MAX_AUDIO_BYTES

logger = logging.getLogger(__name__)


def _stt_unavailable_hint(stt_service) -> str:
    """Tell the user WHY transcription is unavailable, not just that it is.

    The generic message used to send local-Whisper users hunting through
    logs; the common causes are a disabled toggle and a missing optional
    faster-whisper install (default image excludes requirements-optional).
    """
    try:
        settings = stt_service._load_settings() if hasattr(stt_service, "_load_settings") else {}
    except Exception:
        settings = {}
    if settings.get("stt_enabled") is False or settings.get("stt_provider") in (None, "", "disabled"):
        return "STT is disabled — enable it in Settings → Speech to Text"
    if settings.get("stt_provider") == "local":
        try:
            importable = stt_service._is_faster_whisper_importable()
        except Exception:
            importable = True
        if not importable:
            return (
                "Local Whisper needs the faster-whisper package — rebuild with "
                "`docker compose build --build-arg INSTALL_OPTIONAL=true`"
            )
        return "Local Whisper model could not load — see server logs for details"
    return "STT service not available or set to browser mode"


def setup_stt_routes(stt_service, upload_handler=None):
    """Setup STT routes with the provided STT service.

    upload_handler is optional (kept for back-compat with tests that pass
    only stt_service); transcribe-upload requires it.
    """
    router = APIRouter(prefix="/api/stt", tags=["stt"])

    @router.get("/stats")
    async def get_stt_stats():
        """Get STT service statistics"""
        try:
            return stt_service.get_stats()
        except Exception as e:
            logger.error(f"Failed to get STT stats: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/transcribe")
    async def transcribe_audio(file: UploadFile = File(...)):
        """Transcribe uploaded audio file to text.

        Response is additive: {"text": ...} is always present for compat
        with voiceRecorder.js; "language" carries the detected/requested
        language when the backend provides it.
        """
        try:
            if not stt_service.available:
                raise HTTPException(
                    status_code=503,
                    detail={"message": _stt_unavailable_hint(stt_service)}
                )

            audio_bytes = await read_upload_limited(file, STT_MAX_AUDIO_BYTES, "Audio file")
            if not audio_bytes:
                raise HTTPException(status_code=400, detail={"message": "Empty audio file"})

            filename = (file.filename or "") if file else ""
            # CPU-heavy local inference must not block the async event loop
            # (a medium-model transcription can take minutes on a Pi while
            # other requests keep flowing). asyncio.to_thread runs it on a
            # worker thread; /api/stt is exempt from the hard request timeout
            # in app.py for the same reason (cf. /api/image precedent).
            result = await asyncio.to_thread(
                stt_service.transcribe_with_info, audio_bytes, filename
            )
            if result is None:
                raise HTTPException(
                    status_code=500,
                    detail={"message": "Transcription failed"}
                )

            text, language = result
            return {"text": text, "language": language or ""}

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Transcription error: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"message": f"Transcription failed: {str(e)}"}
            )

    @router.post("/transcribe-upload")
    async def transcribe_upload(request: Request):
        """Transcribe an already-uploaded audio attachment by upload ID.

        Phase 1 sync path for short/normal recordings. Body: {"file_id": ...}.
        Ownership is verified via upload_handler.resolve_upload before any
        bytes are read; direct audio attachments for capable models are
        untouched — this only adds a transcription view over the same file.
        """
        if upload_handler is None:
            raise HTTPException(status_code=500, detail={"message": "Upload handler not configured"})
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail={"message": "Request body must be valid JSON"})
        file_id = (body or {}).get("file_id", "")
        if not isinstance(file_id, str) or not file_id:
            raise HTTPException(status_code=400, detail={"message": "file_id is required"})
        if not upload_handler.validate_upload_id(file_id):
            raise HTTPException(status_code=400, detail={"message": "Invalid file ID"})

        if not stt_service.available:
            raise HTTPException(
                status_code=503,
                detail={"message": _stt_unavailable_hint(stt_service)}
            )

        current_user = effective_user(request)
        auth_mgr = getattr(request.app.state, "auth_manager", None)
        info = upload_handler.resolve_upload(
            file_id, owner=current_user, auth_manager=auth_mgr, allow_admin=True
        )
        if info is None:
            # Same 404 shape as download routes to avoid leaking existence.
            raise HTTPException(status_code=404, detail={"message": "File not found"})

        display_name = info.get("name") or info.get("original_name") or file_id
        mime = info.get("mime") or ""
        if not upload_handler.is_audio_file(display_name, mime):
            raise HTTPException(
                status_code=400,
                detail={"message": "Not an audio file. Supported: WAV, MP3, M4A, WEBM, OGG, OPUS, FLAC, AAC, AIFF."}
            )

        path = info.get("path") or ""
        try:
            size = os.path.getsize(path)
        except OSError:
            raise HTTPException(status_code=404, detail={"message": "File not found"})
        if size == 0:
            raise HTTPException(status_code=400, detail={"message": "Empty audio file"})
        if size > STT_MAX_AUDIO_BYTES:
            from src.upload_limits import format_byte_limit
            raise HTTPException(
                status_code=413,
                detail={"message": f"Audio file exceeds {format_byte_limit(STT_MAX_AUDIO_BYTES)} limit"}
            )
        try:
            with open(path, "rb") as f:
                audio_bytes = f.read(STT_MAX_AUDIO_BYTES + 1)
        except OSError as e:
            logger.error(f"Failed to read upload {file_id}: {e}")
            raise HTTPException(status_code=500, detail={"message": "Failed to read audio file"})
        if len(audio_bytes) > STT_MAX_AUDIO_BYTES:
            from src.upload_limits import format_byte_limit
            raise HTTPException(
                status_code=413,
                detail={"message": f"Audio file exceeds {format_byte_limit(STT_MAX_AUDIO_BYTES)} limit"}
            )

        result = await asyncio.to_thread(
            stt_service.transcribe_with_info, audio_bytes, display_name
        )
        if result is None:
            raise HTTPException(status_code=500, detail={"message": "Transcription failed"})
        text, language = result
        return {
            "text": text,
            "language": language or "",
            "file_id": file_id,
            "file_name": display_name,
        }

    return router
