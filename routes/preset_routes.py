"""Preset routes — /api/presets GET, /api/presets/custom POST, user templates CRUD."""

import asyncio
import logging
import uuid
from typing import Dict, Any, List

from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel, Field

from src.request_models import PresetUpdateRequest
from core.middleware import require_admin
from src.auth_helpers import effective_user

logger = logging.getLogger(__name__)


class UserTemplateRequest(BaseModel):
    id: str = ""
    name: str = Field(..., min_length=1, max_length=100)
    system_prompt: str = Field("", max_length=10000)
    temperature: float = Field(1.0, ge=0.0, le=2.0)
    max_tokens: int = Field(0, ge=0, le=65536)


def setup_preset_routes(preset_manager) -> APIRouter:
    router = APIRouter(tags=["presets"])

    @router.get("/api/presets")
    async def get_presets() -> Dict[str, Any]:
        return preset_manager.presets

    @router.post("/api/presets/custom")
    async def update_custom_preset(preset_update: PresetUpdateRequest, _admin: None = Depends(require_admin)) -> Dict[str, Any]:
        try:
            success = preset_manager.update_custom(
                preset_update.temperature,
                preset_update.max_tokens,
                preset_update.system_prompt,
                preset_update.name,
                preset_update.enabled,
                preset_update.inject_prefix,
                preset_update.inject_suffix,
            )
            if success:
                return {"success": True, "message": "Custom preset updated"}
            return {"success": False, "message": "Failed to save preset"}
        except Exception as e:
            logger.error(f"Preset update error: {e}")
            raise HTTPException(500, "Failed to update custom preset")

    @router.get("/api/presets/templates")
    async def get_user_templates() -> List[Dict]:
        return preset_manager.get_user_templates()

    @router.post("/api/presets/templates")
    async def save_user_template(req: UserTemplateRequest, _admin: None = Depends(require_admin)) -> Dict[str, Any]:
        template = req.model_dump()
        if not template["id"]:
            template["id"] = f"user-{uuid.uuid4().hex[:8]}"
        success = preset_manager.save_user_template(template)
        if success:
            return {"success": True, "template": template}
        return {"success": False, "message": "Failed to save template"}

    @router.delete("/api/presets/templates/{template_id}")
    async def delete_user_template(template_id: str, _admin: None = Depends(require_admin)) -> Dict[str, Any]:
        success = preset_manager.delete_user_template(template_id)
        if success:
            return {"success": True}
        return {"success": False, "message": "Failed to delete template"}

    @router.post("/api/presets/expand")
    async def expand_character_prompt(request: Request) -> Dict[str, Any]:
        """Use AI to expand a rough character description into a full system prompt."""
        from src.ai_interaction import _resolve_model
        from src.llm_core import llm_call_async

        data = await request.json()
        draft = (data.get("prompt") or "").strip()
        name = (data.get("name") or "").strip()

        if not draft and not name:
            return {"success": False, "message": "Nothing to expand"}

        user_input = ""
        if name:
            user_input += f"Character name: {name}\n"
        if draft:
            user_input += f"Notes: {draft}\n"

        messages = [
            {"role": "system", "content": (
                "You are an expert at writing character system prompts for AI assistants. "
                "The user will give you a character name and/or rough notes. "
                "Write a concise, effective system prompt (3-6 sentences) that captures the character's personality, "
                "speaking style, knowledge areas, and behavioral guidelines. "
                "Output ONLY the system prompt text — no quotes, no preamble, no explanation."
            )},
            {"role": "user", "content": user_input},
        ]

        try:
            model_spec = data.get("model") or ""
            user = effective_user(request)
            url, model, headers = await asyncio.to_thread(_resolve_model, model_spec, owner=user)
            result = await llm_call_async(url, model, messages, temperature=0.8, max_tokens=500, headers=headers)
            return {"success": True, "prompt": result.strip()}
        except Exception as e:
            logger.error(f"Expand prompt failed: {e}")
            return {"success": False, "message": str(e)}

    # ── Group presets ──
    @router.get("/api/presets/groups")
    async def get_group_presets():
        """Get saved group chat presets."""
        return {"groups": preset_manager.get_group_presets()}

    @router.post("/api/presets/groups")
    async def save_group_presets(request: Request, _admin: None = Depends(require_admin)):
        """Save group chat presets."""
        data = await request.json()
        preset_manager.save_group_presets(data.get("groups", []))
        return {"ok": True}

    return router
