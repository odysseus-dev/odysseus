# routes/prompt_routes.py
"""Saved prompts API — reusable text snippets from the Library Prompts tab."""

import json
import uuid
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.database import SessionLocal, SavedPrompt
from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)


class PromptCreate(BaseModel):
    title: str = "Untitled prompt"
    body: str = ""
    tags: Optional[List[str]] = None


class PromptUpdate(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    tags: Optional[List[str]] = None


def _tags_to_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(t) for t in parsed if str(t).strip()]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def _prompt_to_dict(row: SavedPrompt) -> Dict[str, Any]:
    return {
        "id": row.id,
        "owner": row.owner,
        "title": row.title,
        "body": row.body or "",
        "tags": _tags_to_list(row.tags),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def setup_prompt_routes() -> APIRouter:
    router = APIRouter(prefix="/api/prompts", tags=["prompts"])

    def _owner(request: Request) -> Optional[str]:
        return get_current_user(request)

    def _get_owned(db, prompt_id: str, user: Optional[str]) -> SavedPrompt:
        row = db.query(SavedPrompt).filter(SavedPrompt.id == prompt_id).first()
        if not row:
            raise HTTPException(404, "Prompt not found")
        if user is not None and row.owner != user:
            raise HTTPException(404, "Prompt not found")
        return row

    @router.get("")
    def list_prompts(request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            q = db.query(SavedPrompt)
            if user is not None:
                q = q.filter(SavedPrompt.owner == user)
            rows = q.order_by(SavedPrompt.updated_at.desc()).all()
            return {"prompts": [_prompt_to_dict(r) for r in rows]}
        finally:
            db.close()

    @router.post("")
    def create_prompt(request: Request, body: PromptCreate):
        user = _owner(request)
        title = (body.title or "").strip() or "Untitled prompt"
        db = SessionLocal()
        try:
            row = SavedPrompt(
                id=str(uuid.uuid4()),
                owner=user,
                title=title,
                body=body.body or "",
                tags=json.dumps(body.tags) if body.tags is not None else None,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return _prompt_to_dict(row)
        finally:
            db.close()

    @router.patch("/{prompt_id}")
    def update_prompt(request: Request, prompt_id: str, body: PromptUpdate):
        user = _owner(request)
        db = SessionLocal()
        try:
            row = _get_owned(db, prompt_id, user)
            if body.title is not None:
                row.title = (body.title or "").strip() or "Untitled prompt"
            if body.body is not None:
                row.body = body.body
            if body.tags is not None:
                row.tags = json.dumps(body.tags)
            db.commit()
            db.refresh(row)
            return _prompt_to_dict(row)
        finally:
            db.close()

    @router.delete("/{prompt_id}")
    def delete_prompt(request: Request, prompt_id: str):
        user = _owner(request)
        db = SessionLocal()
        try:
            row = _get_owned(db, prompt_id, user)
            db.delete(row)
            db.commit()
            return {"ok": True}
        finally:
            db.close()

    return router
