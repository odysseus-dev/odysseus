"""Diagnostics routes — /api/db/stats, /api/rag/stats, /api/test/youtube, /api/test-research."""

import logging
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, Form

from services.youtube.youtube_handler import extract_youtube_id, extract_transcript_async
from src.constants import DEFAULT_HOST, SUGGESTED_LOCAL_LLM_URL, IS_DOCKER

logger = logging.getLogger(__name__)


def setup_diagnostics_routes(
    rag_manager,
    rag_available: bool,
    research_handler,
) -> APIRouter:
    router = APIRouter(tags=["diagnostics"])

    @router.get("/api/db/stats")
    async def get_database_stats() -> Dict[str, Any]:
        try:
            from core.database import get_detailed_stats
            return get_detailed_stats()
        except Exception as e:
            logger.error(f"DB stats error: {e}")
            raise HTTPException(500, "Failed to retrieve database statistics")

    @router.get("/api/rag/stats")
    async def get_rag_stats() -> Dict[str, Any]:
        if rag_available and rag_manager:
            return rag_manager.get_stats()
        return {"error": "RAG system not available"}

    @router.get("/api/test/youtube")
    async def test_youtube(url: str) -> Dict[str, Any]:
        try:
            video_id = extract_youtube_id(url)
            if not video_id:
                return {"error": "Invalid YouTube URL"}

            data = await extract_transcript_async(url, video_id)
            return {
                "video_id": video_id,
                "transcript_success": data.get("success", False),
                "transcript_length": len(data.get("transcript", "")) if data.get("success") else 0,
                "transcript_preview": (data.get("transcript", "")[:500] + "...")
                    if data.get("success") and len(data.get("transcript", "")) > 500
                    else data.get("transcript", ""),
                "error": data.get("error") if not data.get("success") else None,
            }
        except Exception as e:
            return {"error": str(e)}

    @router.post("/api/test-research")
    async def test_research(query: str = Form("What is machine learning?")) -> Dict[str, Any]:
        try:
            endpoint = SUGGESTED_LOCAL_LLM_URL.rstrip("/") + "/chat/completions"
            model = "gpt-oss-120b"
            result = await research_handler.call_research_service(query, endpoint, model)
            return {
                "status": "success",
                "query": query,
                "result_preview": result[:200] + "..." if len(result) > 200 else result,
                "result_length": len(result),
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "query": query}

    @router.get("/api/system-info")
    async def get_system_info() -> Dict[str, Any]:
        return {
            "docker": IS_DOCKER,
            "suggested_local_url": SUGGESTED_LOCAL_LLM_URL,
        }

    return router
