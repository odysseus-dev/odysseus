# routes/tts_routes.py
"""
TTS API routes — multi-provider (local Kokoro, API endpoint, browser).
"""

import asyncio
import logging
import re
import threading

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from core.middleware import require_admin
from services.tts.tts_service import KOKORO_VOICES
from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)

class TTSRequest(BaseModel):
    text: str
    format: str = "audio"  # "audio" or "base64"

class VoiceDownloadRequest(BaseModel):
    voice_id: str

# Standard Piper voice ids: en_US-lessac-low etc. Also guards file paths on delete.
_VOICE_ID_RE = re.compile(r"^[A-Za-z0-9_+.\-]+$")

# voice_id -> {"status": "downloading"|"done"|"error", "error": str}
_download_jobs: dict = {}
_download_jobs_lock = threading.Lock()

def setup_tts_routes(tts_service):
    """Setup TTS routes with the provided TTS service"""
    router = APIRouter(prefix="/api/tts", tags=["tts"])

    @router.get("/stats")
    async def get_tts_stats(request: Request):
        """Get TTS service statistics (voice resolved for the caller)"""
        try:
            owner = get_current_user(request) or ""
            return await asyncio.to_thread(tts_service.get_stats, owner)
        except Exception as e:
            logger.error(f"Failed to get TTS stats: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/voices")
    async def list_tts_voices():
        """Installed Piper voices + the static Kokoro voice list for the settings pickers"""
        try:
            voices = await asyncio.to_thread(tts_service.list_voices)
            return {"voices": voices, "kokoro": KOKORO_VOICES}
        except Exception as e:
            logger.error(f"Failed to list TTS voices: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/voices/catalog")
    async def get_voice_catalog(refresh: bool = False):
        """Downloadable Piper voices (official voices.json, cached 24h)"""
        try:
            catalog = await asyncio.to_thread(tts_service.get_piper_catalog, refresh)
            return {"voices": catalog}
        except Exception as e:
            logger.error(f"Failed to fetch Piper voice catalog: {e}")
            raise HTTPException(
                status_code=502,
                detail={"message": "Could not fetch the voice catalog (offline?)"}
            )

    def _run_voice_download(voice_id: str):
        try:
            tts_service.download_piper_voice(voice_id)
            with _download_jobs_lock:
                _download_jobs[voice_id] = {"status": "done", "error": ""}
        except Exception as e:
            logger.error(f"Piper voice download failed ({voice_id}): {e}")
            with _download_jobs_lock:
                _download_jobs[voice_id] = {"status": "error", "error": str(e)}

    @router.post("/voices/download")
    async def download_voice(body: VoiceDownloadRequest, request: Request):
        """Start a background download of a Piper voice (admin only)"""
        require_admin(request)
        voice_id = (body.voice_id or "").strip()
        if not _VOICE_ID_RE.match(voice_id):
            raise HTTPException(status_code=400, detail={"message": "Invalid voice id"})
        with _download_jobs_lock:
            job = _download_jobs.get(voice_id)
            if job and job["status"] == "downloading":
                return {"status": "downloading"}
            _download_jobs[voice_id] = {"status": "downloading", "error": ""}
        threading.Thread(target=_run_voice_download, args=(voice_id,), daemon=True).start()
        return {"status": "downloading"}

    @router.get("/voices/download/{voice_id}/status")
    async def voice_download_status(voice_id: str):
        """Poll a voice download: downloading | done | error | unknown"""
        with _download_jobs_lock:
            job = _download_jobs.get(voice_id)
        if job:
            return {"voice_id": voice_id, **job}
        installed = {v["id"] for v in await asyncio.to_thread(tts_service.list_voices)}
        return {
            "voice_id": voice_id,
            "status": "done" if voice_id in installed else "unknown",
            "error": "",
        }

    @router.delete("/voices/{voice_id}")
    async def delete_voice(voice_id: str, request: Request):
        """Remove an installed Piper voice (admin only)"""
        require_admin(request)
        if not _VOICE_ID_RE.match(voice_id):
            raise HTTPException(status_code=400, detail={"message": "Invalid voice id"})
        deleted = await asyncio.to_thread(tts_service.delete_piper_voice, voice_id)
        if not deleted:
            raise HTTPException(status_code=404, detail={"message": "Voice not installed"})
        return {"success": True}

    @router.post("/synthesize")
    async def synthesize_speech(request: TTSRequest, http_request: Request):
        """Synthesize speech from text"""
        try:
            owner = get_current_user(http_request) or ""
            # Provider resolution + synthesis are CPU-bound and synchronous —
            # keep them off the event loop so one synthesis never stalls the
            # whole server.
            provider, fallback_reason = await asyncio.to_thread(tts_service.effective_provider)
            if provider == "disabled":
                raise HTTPException(
                    status_code=503,
                    detail={"message": "TTS service not available"}
                )
            if provider == "browser":
                # Server can't synthesize — tell the client to use Web Speech
                # instead of failing with an opaque 500.
                raise HTTPException(
                    status_code=503,
                    detail={
                        "message": fallback_reason or "Server TTS unavailable — use browser voices",
                        "fallback": "browser",
                    }
                )
            
            if request.format == "base64":
                audio_b64 = await asyncio.to_thread(
                    tts_service.synthesize_to_base64, request.text, owner
                )
                if not audio_b64:
                    settings = await asyncio.to_thread(tts_service._load_settings, owner)
                    configured = settings.get("tts_provider", "")
                    if configured in ("piper", "local"):
                        raise HTTPException(
                            status_code=503,
                            detail={
                                "message": "Local TTS synthesis failed — using browser voices",
                                "fallback": "browser",
                            },
                        )
                    raise HTTPException(
                        status_code=500,
                        detail={"message": "Synthesis failed"}
                    )
                return {"audio": audio_b64}
            
            else:  # audio format
                audio_data = await asyncio.to_thread(
                    lambda: tts_service.synthesize(request.text, owner=owner)
                )
                if not audio_data:
                    # Piper/Kokoro were configured but synthesis failed at runtime
                    # (corrupt voice, ONNX error, etc.) — degrade to browser TTS
                    # instead of a hard 500 that silences read-aloud.
                    settings = await asyncio.to_thread(tts_service._load_settings, owner)
                    configured = settings.get("tts_provider", "")
                    if configured in ("piper", "local"):
                        raise HTTPException(
                            status_code=503,
                            detail={
                                "message": "Local TTS synthesis failed — using browser voices",
                                "fallback": "browser",
                            },
                        )
                    raise HTTPException(
                        status_code=500,
                        detail={"message": "Synthesis failed"}
                    )
                
                # Detect format from magic bytes (MP3: ID3 tag or sync word ff e0+)
                is_mp3 = audio_data[:3] == b'ID3' or (len(audio_data) >= 2 and audio_data[0] == 0xff and (audio_data[1] & 0xe0) == 0xe0)
                mime = "audio/mpeg" if is_mp3 else "audio/wav"
                return Response(
                    content=audio_data,
                    media_type=mime,
                    headers={
                        "Content-Disposition": "inline; filename=speech.mp3" if "mpeg" in mime else "inline; filename=speech.wav"
                    }
                )
        
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Synthesis error: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"message": f"Synthesis failed: {str(e)}"}
            )

    @router.post("/clear-cache")
    async def clear_tts_cache():
        """Clear TTS cache"""
        try:
            tts_service.clear_cache()
            return {"success": True, "message": "Cache cleared"}
        except Exception as e:
            logger.error(f"Failed to clear cache: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    return router
