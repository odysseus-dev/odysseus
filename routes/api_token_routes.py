"""API Token management routes — /api/tokens/*."""

import secrets
import uuid

import bcrypt
from fastapi import APIRouter, Form, HTTPException, Request

from core.database import ApiToken, get_db_session
from core.middleware import require_admin
from src.auth_helpers import get_current_user

MAX_NAME_LEN = 100
DEFAULT_SCOPES = "chat"


def setup_api_token_routes() -> APIRouter:
    router = APIRouter(prefix="/api", tags=["api_tokens"])

    @router.get("/tokens")
    def list_tokens(request: Request):
        require_admin(request)
        with get_db_session() as db:
            tokens = db.query(ApiToken).all()
            return [
                {
                    "id": t.id,
                    "name": t.name,
                    "owner": getattr(t, "owner", None),
                    "token_prefix": t.token_prefix,
                    "scopes": [
                        s.strip()
                        for s in (getattr(t, "scopes", "") or DEFAULT_SCOPES).split(",")
                        if s.strip()
                    ],
                    "is_active": t.is_active,
                    "last_used_at": t.last_used_at.isoformat()
                    if t.last_used_at
                    else None,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in tokens
            ]

    def _invalidate_cache(request: Request):
        """Tell the auth middleware its cached token map is stale."""
        try:
            invalidator = getattr(request.app.state, "invalidate_token_cache", None)
            if invalidator:
                invalidator()
        except Exception:
            pass

    @router.post("/tokens")
    def create_token(request: Request, name: str = Form("")):
        require_admin(request)
        name = name.strip()[:MAX_NAME_LEN]
        if not name:
            raise HTTPException(400, "Token name is required")
        owner = get_current_user(request)

        raw_token = "ody_" + secrets.token_urlsafe(32)
        token_hash = bcrypt.hashpw(raw_token.encode(), bcrypt.gensalt()).decode()
        token_id = str(uuid.uuid4())[:8]

        with get_db_session() as db:
            db.add(
                ApiToken(
                    id=token_id,
                    owner=owner,
                    name=name,
                    token_hash=token_hash,
                    token_prefix=raw_token[:8],
                    scopes=DEFAULT_SCOPES,
                    is_active=True,
                )
            )
        _invalidate_cache(request)

        return {
            "id": token_id,
            "name": name,
            "owner": owner,
            "token": raw_token,
            "token_prefix": raw_token[:8],
            "scopes": DEFAULT_SCOPES.split(","),
        }

    @router.delete("/tokens/{token_id}")
    def delete_token(request: Request, token_id: str):
        require_admin(request)
        with get_db_session() as db:
            deleted = db.query(ApiToken).filter(ApiToken.id == token_id).delete()
            if not deleted:
                raise HTTPException(404, "Token not found")
        _invalidate_cache(request)
        return {"status": "deleted"}

    return router
