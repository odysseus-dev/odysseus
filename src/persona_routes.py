"""
persona_routes.py

REST API for managing Personas (Sub-Agents).

Provides CRUD + activation endpoints that can be called from UI and from the agent itself.
"""

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.persona_manager import PersonaManager, Persona
from src.auth_helpers import get_current_user
from core.middleware import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/personas", tags=["personas"])

pm = PersonaManager()


class PersonaCreateRequest(BaseModel):
    name: str = Field(..., max_length=80)
    display_name: Optional[str] = None
    description: str = ""
    category: str = "general"
    allowed_tools: List[str] = Field(default_factory=list)
    allowed_skills: List[str] = Field(default_factory=list)
    system_prompt_addition: str = ""
    temperature: float = 0.4
    personality: str = ""


class PersonaUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    allowed_tools: Optional[List[str]] = None
    system_prompt_addition: Optional[str] = None
    temperature: Optional[float] = None
    personality: Optional[str] = None
    status: Optional[str] = None


@router.get("", response_model=List[dict])
async def list_personas(request: Request, include_disabled: bool = False):
    user = get_current_user(request)
    personas = pm.list_personas(include_disabled=include_disabled)
    return [p.to_dict() for p in personas]


@router.get("/{name}")
async def get_persona(name: str, request: Request):
    user = get_current_user(request)
    p = pm.get_persona(name)
    if not p:
        raise HTTPException(404, f"Persona '{name}' not found")
    return p.to_dict()


@router.post("")
async def create_persona(req: PersonaCreateRequest, request: Request):
    user = get_current_user(request)
    if pm.get_persona(req.name):
        raise HTTPException(400, f"Persona '{req.name}' already exists")

    p = Persona(
        name=req.name.lower().replace(" ", "-"),
        display_name=req.display_name or req.name.title(),
        description=req.description,
        category=req.category,
        allowed_tools=req.allowed_tools,
        allowed_skills=req.allowed_skills,
        system_prompt_addition=req.system_prompt_addition,
        temperature=req.temperature,
        personality=req.personality,
        source="user",
    )
    pm.save_persona(p, actor=user or "user")
    return {"success": True, "persona": p.to_dict()}


@router.patch("/{name}")
async def update_persona(name: str, req: PersonaUpdateRequest, request: Request):
    user = get_current_user(request)
    p = pm.get_persona(name)
    if not p:
        raise HTTPException(404, f"Persona '{name}' not found")

    if req.display_name is not None:
        p.display_name = req.display_name
    if req.description is not None:
        p.description = req.description
    if req.allowed_tools is not None:
        p.allowed_tools = req.allowed_tools
    if req.system_prompt_addition is not None:
        p.system_prompt_addition = req.system_prompt_addition
    if req.temperature is not None:
        p.temperature = req.temperature
    if req.personality is not None:
        p.personality = req.personality
    if req.status is not None:
        p.status = req.status

    pm.save_persona(p, actor=user or "user")
    return {"success": True, "persona": p.to_dict()}


@router.delete("/{name}")
async def delete_persona(name: str, request: Request):
    user = get_current_user(request)
    if pm.delete_persona(name):
        return {"success": True}
    raise HTTPException(404, f"Persona '{name}' not found")


@router.post("/{name}/activate")
async def activate_persona(name: str, request: Request):
    user = get_current_user(request)
    p = pm.get_persona(name)
    if not p:
        raise HTTPException(404, f"Persona '{name}' not found")
    p.status = "active"
    pm.save_persona(p, actor=user or "user")
    return {"success": True, "status": "active"}


@router.post("/{name}/deactivate")
async def deactivate_persona(name: str, request: Request):
    user = get_current_user(request)
    p = pm.get_persona(name)
    if not p:
        raise HTTPException(404, f"Persona '{name}' not found")
    p.status = "disabled"
    pm.save_persona(p, actor=user or "user")
    return {"success": True, "status": "disabled"}


@router.post("/seed-defaults")
async def seed_default_personas(request: Request):
    """Create the built-in default personas (researcher, coder, assistant) if they don't exist."""
    user = get_current_user(request)
    created = pm.get_or_create_default_personas()
    return {
        "success": True,
        "created": [p.name for p in created],
        "total": len(pm.list_personas())
    }


@router.post("/set-active")
async def set_active_persona(request: Request, body: dict):
    """Lightweight way to set active persona for the current conversation."""
    user = get_current_user(request)
    name = body.get("name")
    session_id = body.get("session_id")
    
    if not name:
        raise HTTPException(400, "name is required")
    
    p = pm.get_persona(name)
    if not p or p.status != "active":
        raise HTTPException(404, f"Persona '{name}' not found or disabled")
    
    # For now we just return success. In a real implementation this would be stored
    # per session in the database or in-memory.
    pm.record_usage(name)
    
    return {
        "success": True,
        "active_persona": name,
        "display_name": p.display_name,
        "message": f"Persona '{p.display_name}' is now active for this session"
    }
