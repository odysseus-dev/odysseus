"""
github_routes.py

REST endpoints for the per-user GitHub integration: save / clear the Personal
Access Token and toggle the master enable flag. The actual GitHub API calls
live in `mcp_servers/github_server.py`; this file just manages the integration
config row in the DB.

Auth model: every endpoint takes the current Odysseus user via `require_user`
(matches the email_routes pattern). Each user owns one GitHub integration row
keyed by username.

PAT storage: encrypted at rest via `src/secret_storage.py`. The plaintext PAT
is only present in memory during a save (to validate against GitHub) and inside
the github MCP subprocess when it builds an auth header. We never echo the PAT
back in API responses — the GET endpoint returns metadata only.
"""

import logging
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.database import SessionLocal, GitHubIntegration
from routes.email_helpers import require_user
from src.secret_storage import encrypt as _encrypt_secret

logger = logging.getLogger(__name__)

GH_API = "https://api.github.com"
GH_TIMEOUT = 15.0
USER_AGENT = "Odysseus-Integration/0.1"


# ── Pydantic request bodies ──

class SavePATRequest(BaseModel):
    pat: str = Field(..., min_length=1, description="GitHub Personal Access Token. Validated against /user before save.")


class UpdateFlagsRequest(BaseModel):
    enabled: bool | None = None


# ── Helpers ──

def _row_to_dict(row: GitHubIntegration) -> dict:
    """Public view of the integration row. NEVER includes the PAT."""
    return {
        "configured": True,
        "github_username": row.github_username,
        "enabled": bool(row.enabled),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def _validate_pat(pat: str) -> dict:
    """Hit GitHub /user with the PAT. Returns the user object on success.
    Raises HTTPException(400) with a useful message on failure so the
    settings UI can surface what went wrong."""
    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    try:
        async with httpx.AsyncClient(timeout=GH_TIMEOUT) as client:
            resp = await client.get(f"{GH_API}/user", headers=headers)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach GitHub: {e}")
    if resp.status_code == 401:
        raise HTTPException(status_code=400, detail="GitHub rejected the token (401). Check the PAT value and scopes.")
    if resp.status_code >= 400:
        raise HTTPException(status_code=400, detail=f"GitHub returned {resp.status_code}: {resp.text[:160]}")
    try:
        return resp.json()
    except Exception:
        raise HTTPException(status_code=502, detail="GitHub returned a non-JSON response.")


# ── Router setup ──

def setup_github_routes(mcp_manager=None):
    """Build the router. `mcp_manager` is optional; if passed, we restart the
    github MCP server after a PAT change so its in-process PAT cache flushes.
    Without it, a server restart is needed for new PATs to take effect."""
    router = APIRouter(prefix="/api/github", tags=["github"])

    async def _restart_github_mcp(owner: str):
        if not mcp_manager:
            return
        try:
            # The github MCP server caches the PAT in-process on first use.
            # After a save we want it to re-read from the DB. Easiest path is
            # to disconnect; the next github tool call reconnects.
            if hasattr(mcp_manager, "disconnect_server"):
                await mcp_manager.disconnect_server("github")
        except Exception as e:
            logger.warning(f"github MCP restart after PAT save failed (non-fatal): {e}")

    @router.get("/integration")
    def get_integration(request: Request):
        """Return the current user's integration metadata, or
        {configured: false} if none. Never returns the PAT."""
        owner = require_user(request)
        with SessionLocal() as db:
            row = db.query(GitHubIntegration).filter_by(owner=owner or "").first()
        if not row:
            return {"configured": False}
        return _row_to_dict(row)

    @router.post("/integration")
    async def save_integration(request: Request, body: SavePATRequest):
        """Save (or replace) the user's PAT. Validates the token against
        /user first; on success persists encrypted PAT + username. On
        failure returns 400 with GitHub's error message."""
        owner = require_user(request)
        pat = body.pat.strip()
        if not pat:
            raise HTTPException(400, "PAT is empty.")
        user_obj = await _validate_pat(pat)
        gh_username = user_obj.get("login") or "unknown"
        enc = _encrypt_secret(pat)
        with SessionLocal() as db:
            row = db.query(GitHubIntegration).filter_by(owner=owner or "").first()
            if row:
                row.pat_encrypted = enc
                row.github_username = gh_username
                row.enabled = True
                row.updated_at = datetime.utcnow()
            else:
                row = GitHubIntegration(
                    owner=owner or "",
                    pat_encrypted=enc,
                    github_username=gh_username,
                    enabled=True,
                )
                db.add(row)
            db.commit()
            db.refresh(row)
            payload = _row_to_dict(row)
        await _restart_github_mcp(owner or "")
        return payload

    @router.delete("/integration")
    async def delete_integration(request: Request):
        """Remove the integration entirely. PAT, username, everything — gone."""
        owner = require_user(request)
        with SessionLocal() as db:
            row = db.query(GitHubIntegration).filter_by(owner=owner or "").first()
            if row:
                db.delete(row)
                db.commit()
        await _restart_github_mcp(owner or "")
        return {"configured": False}

    @router.post("/integration/flags")
    def update_flags(request: Request, body: UpdateFlagsRequest):
        """Update the master enabled toggle."""
        owner = require_user(request)
        with SessionLocal() as db:
            row = db.query(GitHubIntegration).filter_by(owner=owner or "").first()
            if not row:
                raise HTTPException(404, "GitHub integration not configured.")
            if body.enabled is not None:
                row.enabled = bool(body.enabled)
            row.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(row)
            return _row_to_dict(row)

    return router
