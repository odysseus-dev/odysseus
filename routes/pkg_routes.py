"""Package extension API routes — LLM call endpoint for package widgets."""
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)


class LLMBody(BaseModel):
    system: str = ""
    message: str
    model: str = ""
    max_tokens: int = 1024


def setup_pkg_routes() -> APIRouter:
    router = APIRouter(prefix="/api/pkg", tags=["pkg"])

    @router.post("/llm")
    async def pkg_llm(body: LLMBody, request: Request):
        """
        Non-streaming LLM call for package widgets.
        Uses the user's configured default model when no model is specified.
        """
        owner = get_current_user(request)
        if not owner:
            raise HTTPException(status_code=401, detail="Authentication required")
        if not body.message.strip():
            raise HTTPException(status_code=400, detail="message is required")

        messages = []
        if body.system:
            messages.append({"role": "system", "content": body.system})
        messages.append({"role": "user", "content": body.message})

        try:
            if body.model:
                # Specific model requested — resolve by name
                from src.ai_interaction import _resolve_model
                url, model_id, headers = _resolve_model(body.model, owner)
            else:
                # Use the user's default model via settings
                from src.endpoint_resolver import resolve_endpoint
                url, model_id, headers = resolve_endpoint("default", owner=owner)
                if not url or not model_id:
                    raise ValueError("No default model configured. Set one in Settings → Models.")

            from src.llm_core import llm_call_async
            content = await llm_call_async(
                url, model_id, messages,
                headers=headers or {},
                max_tokens=body.max_tokens,
            )
            return {"content": content, "model": model_id}

        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            logger.error("pkg_llm error for owner=%s: %s", owner, e)
            raise HTTPException(status_code=500, detail=str(e))

    return router
