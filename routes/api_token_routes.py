"""API Token management routes — /api/tokens/*."""

import os
import secrets
import uuid

import bcrypt
from fastapi import APIRouter, HTTPException, Request, Form

from core.database import get_db_session, ApiToken
from core.middleware import require_admin
from src.auth_helpers import get_current_user, require_user

MAX_NAME_LEN = 100
DEFAULT_SCOPES = "chat"
COOKBOOK_SCOPES = {"cookbook:read", "cookbook:launch"}
ALLOWED_SCOPES = {
    "chat",
    "todos:read",
    "todos:write",
    "documents:read",
    "documents:write",
    "email:read",
    "email:draft",
    "email:send",
    "calendar:read",
    "calendar:write",
    "memory:read",
    "memory:write",
    "cookbook:read",
    "cookbook:launch",
}
TOKEN_PROFILES = {
    "chat": ["chat"],
    "codex_todos": ["todos:read", "todos:write"],
    "codex_documents": ["documents:read", "documents:write"],
    "codex_email_drafts": ["email:read", "email:draft", "documents:read", "documents:write"],
}


def _normalize_scopes(scopes: str | list[str] | None = None, profile: str | None = None) -> list[str]:
    profile = profile if isinstance(profile, str) else None
    profile_key = (profile or "").strip()
    if profile_key:
        if profile_key not in TOKEN_PROFILES:
            raise HTTPException(400, "Unknown token profile")
        requested = list(TOKEN_PROFILES[profile_key])
    elif isinstance(scopes, list):
        requested = [str(s).strip() for s in scopes if str(s).strip()]
    elif isinstance(scopes, str) and scopes:
        requested = [s.strip() for s in scopes.replace(" ", ",").split(",") if s.strip()]
    else:
        requested = [DEFAULT_SCOPES]

    normalized = []
    for scope in requested:
        if scope not in ALLOWED_SCOPES:
            raise HTTPException(400, f"Unknown token scope: {scope}")
        if scope not in normalized:
            normalized.append(scope)

    def ensure_before(write_scope: str, read_scope: str):
        if write_scope not in normalized or read_scope in normalized:
            return
        idx = normalized.index(write_scope)
        normalized.insert(idx, read_scope)

    ensure_before("todos:write", "todos:read")
    ensure_before("documents:write", "documents:read")
    ensure_before("calendar:write", "calendar:read")
    ensure_before("memory:write", "memory:read")
    ensure_before("email:draft", "email:read")
    ensure_before("cookbook:launch", "cookbook:read")

    return normalized or [DEFAULT_SCOPES]


def _require_self_token_user(request: Request) -> str:
    if os.getenv("AUTH_ENABLED", "true").lower() == "false":
        raise HTTPException(403, "Personal API tokens require AUTH_ENABLED=true")
    user = require_user(request)
    if not user:
        raise HTTPException(401, "Personal API tokens require an authenticated user")
    return user


def _validate_self_scopes(request: Request, user: str, scopes: list[str]) -> None:
    if not COOKBOOK_SCOPES.intersection(scopes):
        return
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    if not auth_mgr or not auth_mgr.is_admin(user):
        raise HTTPException(403, "Cookbook API token scopes require an admin account")


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
                    "scopes": [s.strip() for s in (getattr(t, "scopes", "") or DEFAULT_SCOPES).split(",") if s.strip()],
                    "is_active": t.is_active,
                    "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
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

    @router.get("/tokens/profiles")
    def token_profiles(request: Request):
        require_admin(request)
        return {
            "profiles": TOKEN_PROFILES,
            "allowed_scopes": sorted(ALLOWED_SCOPES),
        }

    @router.post("/tokens")
    def create_token(
        request: Request,
        name: str = Form(""),
        scopes: str = Form(None),
        profile: str = Form(None),
    ):
        require_admin(request)
        name = name.strip()[:MAX_NAME_LEN]
        if not name:
            raise HTTPException(400, "Token name is required")
        owner = get_current_user(request)
        scope_list = _normalize_scopes(scopes, profile)
        scopes_value = ",".join(scope_list)

        raw_token = "ody_" + secrets.token_urlsafe(32)
        token_hash = bcrypt.hashpw(raw_token.encode(), bcrypt.gensalt()).decode()
        token_id = str(uuid.uuid4())[:8]

        with get_db_session() as db:
            db.add(ApiToken(
                id=token_id,
                owner=owner,
                name=name,
                token_hash=token_hash,
                token_prefix=raw_token[:8],
                scopes=scopes_value,
                is_active=True,
            ))
        _invalidate_cache(request)

        return {
            "id": token_id,
            "name": name,
            "owner": owner,
            "token": raw_token,
            "token_prefix": raw_token[:8],
            "scopes": scope_list,
        }

    @router.patch("/tokens/{token_id}")
    async def update_token(request: Request, token_id: str):
        require_admin(request)
        current_user = get_current_user(request)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        with get_db_session() as db:
            token = db.query(ApiToken).filter(ApiToken.id == token_id).first()
            if not token:
                raise HTTPException(404, "Token not found")
            if current_user and token.owner != current_user:
                raise HTTPException(403, "Not your token")
            if isinstance(payload.get("name"), str) and payload["name"].strip():
                token.name = payload["name"].strip()[:MAX_NAME_LEN]
            # Only touch scopes when the caller actually sent them. A partial
            # update such as a rename ({"name": ...} with no "scopes" key) must
            # not silently reset the token to the default scope — that dropped
            # every previously granted scope.
            if "scopes" in payload:
                token.scopes = ",".join(_normalize_scopes(payload.get("scopes")))
            db.add(token)
            current_scopes = [
                s.strip()
                for s in (getattr(token, "scopes", "") or DEFAULT_SCOPES).split(",")
                if s.strip()
            ]
            response = {
                "id": token_id,
                "name": getattr(token, "name", ""),
                "owner": getattr(token, "owner", None),
                "token_prefix": getattr(token, "token_prefix", ""),
                "scopes": current_scopes,
            }
        _invalidate_cache(request)
        return response

    @router.delete("/tokens/{token_id}")
    def delete_token(request: Request, token_id: str):
        require_admin(request)
        current_user = get_current_user(request)
        with get_db_session() as db:
            token = db.query(ApiToken).filter(ApiToken.id == token_id).first()
            if not token:
                raise HTTPException(404, "Token not found")
            if current_user and token.owner != current_user:
                raise HTTPException(403, "Not your token")
            db.delete(token)
        _invalidate_cache(request)
        return {"status": "deleted"}

    # ── Self-serve endpoints (cookie-only, owner forced to current user) ──

    @router.get("/tokens/self")
    def list_self_tokens(request: Request):
        """List the current user's own API tokens. Cookie session only."""
        user = _require_self_token_user(request)
        with get_db_session() as db:
            tokens = db.query(ApiToken).filter(
                ApiToken.owner == user, ApiToken.is_active == True  # noqa: E712
            ).all()
            return [
                {
                    "id": t.id,
                    "name": t.name,
                    "owner": getattr(t, "owner", None),
                    "token_prefix": t.token_prefix,
                    "scopes": [s.strip() for s in (getattr(t, "scopes", "") or DEFAULT_SCOPES).split(",") if s.strip()],
                    "is_active": t.is_active,
                    "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in tokens
            ]

    @router.post("/tokens/self")
    def create_self_token(
        request: Request,
        name: str = Form(""),
        scopes: str = Form(None),
        profile: str = Form(None),
    ):
        """Create an API token for the current user. Cookie session only."""
        user = _require_self_token_user(request)
        name = name.strip()[:MAX_NAME_LEN]
        if not name:
            raise HTTPException(400, "Token name is required")
        scope_list = _normalize_scopes(scopes, profile)
        _validate_self_scopes(request, user, scope_list)
        scopes_value = ",".join(scope_list)

        raw_token = "ody_" + secrets.token_urlsafe(32)
        token_hash = bcrypt.hashpw(raw_token.encode(), bcrypt.gensalt()).decode()
        token_id = str(uuid.uuid4())[:8]

        with get_db_session() as db:
            db.add(ApiToken(
                id=token_id,
                owner=user,
                name=name,
                token_hash=token_hash,
                token_prefix=raw_token[:8],
                scopes=scopes_value,
                is_active=True,
            ))
        _invalidate_cache(request)

        return {
            "id": token_id,
            "name": name,
            "owner": user,
            "token": raw_token,
            "token_prefix": raw_token[:8],
            "scopes": scope_list,
        }

    @router.delete("/tokens/self/{token_id}")
    def delete_self_token(request: Request, token_id: str):
        """Delete one of the current user's own API tokens. Cookie session only."""
        user = _require_self_token_user(request)
        with get_db_session() as db:
            token = db.query(ApiToken).filter(ApiToken.id == token_id).first()
            # Return 404 for both "not found" and "not yours" so callers
            # can't probe for the existence of another user's token ids.
            if not token or token.owner != user:
                raise HTTPException(404, "Token not found")
            db.delete(token)
        _invalidate_cache(request)
        return {"status": "deleted"}

    return router
