# routes/tts_routes.py
"""
TTS API routes — multi-provider (local Kokoro, API endpoint, browser).
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
import logging

logger = logging.getLogger(__name__)

class TTSRequest(BaseModel):
    text: str
    format: str = "audio"  # "audio" or "base64"
    engine: str | None = None  # "supertonic" for Fugassa GM
    lang: str | None = None
    speaker_id: int | None = Field(default=None, ge=0, le=9)
    speed: float | None = Field(default=None, gt=0, le=3.0)

def setup_tts_routes(tts_service):
    """Setup TTS routes with the provided TTS service"""
    router = APIRouter(prefix="/api/tts", tags=["tts"])

    @router.get("/stats")
    async def get_tts_stats():
        """Get TTS service statistics"""
        try:
            return tts_service.get_stats()
        except Exception as e:
            logger.error(f"Failed to get TTS stats: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/voices")
    async def list_tts_voices(engine: str = "supertonic", lang: str = "cs"):
        """List TTS voices for an engine (Fugassa: supertonic)."""
        if engine != "supertonic":
            raise HTTPException(status_code=400, detail={"message": f"Unknown engine: {engine}"})
        try:
            voices = tts_service.list_supertonic_voices(lang)
            return {
                "engine": engine,
                "lang": lang,
                "ready": tts_service.supertonic_available(),
                "voices": voices,
            }
        except Exception as e:
            logger.error(f"Failed to list TTS voices: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/synthesize")
    async def synthesize_speech(request: TTSRequest):
        """Synthesize speech from text"""
        try:
            if request.engine == "supertonic":
                if not tts_service.supertonic_available():
                    raise HTTPException(
                        status_code=503,
                        detail={"message": "Supertonic TTS model not available"},
                    )
                audio_data = tts_service.synthesize_supertonic(
                    request.text,
                    lang=request.lang or "cs",
                    speaker_id=request.speaker_id if request.speaker_id is not None else 0,
                    speed=request.speed if request.speed is not None else 1.0,
                )
            else:
                if not tts_service.available:
                    raise HTTPException(
                        status_code=503,
                        detail={"message": "TTS service not available"}
                    )
                audio_data = tts_service.synthesize(request.text)
            
            if request.format == "base64":
                if not audio_data:
                    raise HTTPException(
                        status_code=500,
                        detail={"message": "Synthesis failed"}
                    )
                import base64
                audio_b64 = base64.b64encode(audio_data).decode("utf-8")
                return {"audio": audio_b64}
            
            if not audio_data:
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
