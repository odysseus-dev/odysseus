# routes/stt_routes.py
"""STT API routes — local Whisper, Groq, or any OpenAI-compatible endpoint."""

from fastapi import APIRouter, HTTPException, UploadFile, File
import logging

from src.upload_limits import read_upload_limited, STT_MAX_AUDIO_BYTES

logger = logging.getLogger(__name__)


def setup_stt_routes(stt_service):
    """Setup STT routes with the provided STT service"""
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
    async def transcribe_audio(request: Request, file: UploadFile = File(...)):
        """Transcribe uploaded audio and return the transcript text."""
        require_user(request)
        try:
            audio_bytes = await read_upload_limited(file, STT_MAX_AUDIO_BYTES, "Audio file")
            if not audio_bytes:
                raise HTTPException(status_code=400, detail={"message": "Empty audio file"})

            try:
                text = stt_service.transcribe(audio_bytes)
            except ValueError as e:
                # Configuration errors (e.g. missing API key)
                raise HTTPException(status_code=422, detail={"message": str(e)})
            except Exception as e:
                raise HTTPException(
                    status_code=502,
                    detail={"message": f"Transcription provider error: {e}"}
                )

            if text is None:
                raise HTTPException(
                    status_code=503,
                    detail={
                        "message": (
                            "STT not configured. "
                            "Go to Settings → AI Defaults → Speech-to-Text and choose a provider."
                        )
                    },
                )

            return {"text": text}

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Transcription error: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"message": f"Transcription failed: {e}"},
            )

    return router
