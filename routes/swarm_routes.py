"""
swarm_routes.py — API for managing custom and built-in Swarm configurations.
"""

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.database import SessionLocal, SwarmConfig, SwarmRun
from src.auth_helpers import require_user
from src.swarm.swarm_types import SwarmDefinition
from src.swarm.swarm_definitions import list_builtin_swarms, get_builtin_swarm

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/swarms", tags=["swarms"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class SwarmRoleModel(BaseModel):
    name: str
    slug: str
    description: str = ""
    system_prompt: str = ""
    tools_allowed: List[str] = Field(default_factory=lambda: ["all"])
    tools_denied: List[str] = Field(default_factory=list)
    model: Optional[str] = None
    endpoint_url: Optional[str] = None
    priority: int = 0


class SwarmDefinitionModel(BaseModel):
    name: str
    description: str = ""
    domain: str = "general"
    master: SwarmRoleModel
    workers: List[SwarmRoleModel]
    routing_rules: Dict[str, List[str]] = Field(default_factory=dict)
    memory_config: Dict[str, Any] = Field(default_factory=lambda: {"shared": True, "persist_after": True})
    max_parallel: int = 5
    version: str = "1.0.0"


class SwarmResponse(BaseModel):
    id: str
    name: str
    description: str
    domain: str
    is_builtin: bool
    definition: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=List[SwarmResponse])
def list_swarms(user: str = Depends(require_user)):
    """List all available swarms (built-in + custom)."""
    db = SessionLocal()
    try:
        # User's custom swarms
        custom_swarms = db.query(SwarmConfig).filter(
            SwarmConfig.owner == user,
            SwarmConfig.is_active == True
        ).all()
        
        results = []
        for s in custom_swarms:
            results.append({
                "id": s.id,
                "name": s.name,
                "description": s.description or "",
                "domain": s.domain or "general",
                "is_builtin": False,
            })
            
        # Built-in swarms
        results.extend(list_builtin_swarms())
        return results
    finally:
        db.close()


@router.get("/{swarm_id}")
def get_swarm(swarm_id: str, user: str = Depends(require_user)):
    """Get a full swarm definition by ID."""
    builtin = get_builtin_swarm(swarm_id)
    if builtin:
        return {
            "id": builtin.id,
            "name": builtin.name,
            "description": builtin.description,
            "domain": builtin.domain,
            "is_builtin": True,
            "definition": builtin.to_dict(),
        }

    db = SessionLocal()
    try:
        swarm = db.query(SwarmConfig).filter(
            SwarmConfig.id == swarm_id,
            SwarmConfig.owner == user,
            SwarmConfig.is_active == True
        ).first()
        
        if not swarm:
            raise HTTPException(status_code=404, detail="Swarm not found")
            
        return {
            "id": swarm.id,
            "name": swarm.name,
            "description": swarm.description,
            "domain": swarm.domain,
            "is_builtin": False,
            "definition": json.loads(swarm.definition),
        }
    finally:
        db.close()


@router.post("")
def create_swarm(payload: SwarmDefinitionModel, user: str = Depends(require_user)):
    """Create a new custom swarm."""
    db = SessionLocal()
    try:
        swarm_id = uuid.uuid4().hex
        
        # Hydrate to SwarmDefinition to ensure validity
        data = payload.dict()
        data["id"] = swarm_id
        definition = SwarmDefinition.from_dict(data)
        
        config = SwarmConfig(
            id=swarm_id,
            owner=user,
            name=payload.name,
            description=payload.description,
            domain=payload.domain,
            definition=json.dumps(definition.to_dict()),
            is_builtin=False,
            is_active=True,
            version=payload.version,
        )
        db.add(config)
        db.commit()
        return {"id": swarm_id, "status": "created"}
    except Exception as e:
        db.rollback()
        logger.exception("Failed to create swarm")
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@router.put("/{swarm_id}")
def update_swarm(swarm_id: str, payload: SwarmDefinitionModel, user: str = Depends(require_user)):
    """Update an existing custom swarm."""
    db = SessionLocal()
    try:
        swarm = db.query(SwarmConfig).filter(
            SwarmConfig.id == swarm_id,
            SwarmConfig.owner == user
        ).first()
        
        if not swarm:
            raise HTTPException(status_code=404, detail="Swarm not found")
        if swarm.is_builtin:
            raise HTTPException(status_code=400, detail="Cannot edit built-in swarms")

        data = payload.dict()
        data["id"] = swarm_id
        definition = SwarmDefinition.from_dict(data)

        swarm.name = payload.name
        swarm.description = payload.description
        swarm.domain = payload.domain
        swarm.definition = json.dumps(definition.to_dict())
        swarm.version = payload.version
        
        db.commit()
        return {"id": swarm_id, "status": "updated"}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@router.delete("/{swarm_id}")
def delete_swarm(swarm_id: str, user: str = Depends(require_user)):
    """Soft-delete a custom swarm."""
    db = SessionLocal()
    try:
        swarm = db.query(SwarmConfig).filter(
            SwarmConfig.id == swarm_id,
            SwarmConfig.owner == user
        ).first()
        
        if not swarm:
            raise HTTPException(status_code=404, detail="Swarm not found")
        if swarm.is_builtin:
            raise HTTPException(status_code=400, detail="Cannot delete built-in swarms")

        swarm.is_active = False
        db.commit()
        return {"id": swarm_id, "status": "deleted"}
    finally:
        db.close()


@router.get("/runs/history")
def list_runs(limit: int = 50, user: str = Depends(require_user)):
    """List recent swarm executions."""
    db = SessionLocal()
    try:
        runs = db.query(SwarmRun).filter(
            SwarmRun.owner == user
        ).order_by(SwarmRun.created_at.desc()).limit(limit).all()
        
        return [
            {
                "id": r.id,
                "swarm_id": r.swarm_id,
                "session_id": r.session_id,
                "status": r.status,
                "user_query": r.user_query,
                "total_tokens": r.total_tokens,
                "duration_ms": r.duration_ms,
                "created_at": r.created_at.isoformat(),
            }
            for r in runs
        ]
    finally:
        db.close()


@router.get("/runs/{run_id}")
def get_run(run_id: str, user: str = Depends(require_user)):
    """Get details of a specific swarm execution."""
    db = SessionLocal()
    try:
        run = db.query(SwarmRun).filter(
            SwarmRun.id == run_id,
            SwarmRun.owner == user
        ).first()
        
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
            
        return {
            "id": run.id,
            "swarm_id": run.swarm_id,
            "session_id": run.session_id,
            "status": run.status,
            "user_query": run.user_query,
            "master_plan": json.loads(run.master_plan) if run.master_plan else {},
            "worker_results": json.loads(run.worker_results) if run.worker_results else [],
            "final_response": run.final_response,
            "total_tokens": run.total_tokens,
            "duration_ms": run.duration_ms,
            "workers_activated": run.workers_activated,
            "workers_skipped": run.workers_skipped,
            "created_at": run.created_at.isoformat(),
        }
    finally:
        db.close()
