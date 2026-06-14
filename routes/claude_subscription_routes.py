"""Claude Subscription setup route.

The Claude subscription OAuth (``claude setup-token`` / the Claude Code CLI)
redirects to a localhost loopback, which a browser can't reach for a remote
server — so the user runs that OAuth locally and pastes the resulting token
here. One admin-only endpoint validates the token and provisions the endpoint:

  POST /api/claude-subscription/complete  (form: token) -> { id, name, base_url, models }

Only the access/refresh tokens are persisted (encrypted at rest via
ProviderAuthSession).
"""

import json
import logging
import uuid
from datetime import timedelta
from typing import Dict, Optional

from fastapi import APIRouter, Form, HTTPException, Request

from core.database import ModelEndpoint, ProviderAuthSession, SessionLocal, utcnow_naive
from core.middleware import require_admin
from src import claude_subscription
from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)


def _provision_endpoint(access_token, refresh_token, expires_at, owner: Optional[str]) -> Dict:
    if not access_token:
        raise ValueError("No access token found in the pasted value.")

    base = claude_subscription.DEFAULT_CLAUDE_SUBSCRIPTION_BASE_URL
    models = claude_subscription.fetch_available_models(access_token)
    if not models:
        raise claude_subscription.ClaudeSubscriptionReauthRequired(
            "Token did not authorize any Claude models — it may be invalid or expired."
        )

    if expires_at is None:
        expires_at = utcnow_naive() + timedelta(days=claude_subscription.CLAUDE_DEFAULT_TOKEN_TTL_DAYS)

    db = SessionLocal()
    try:
        auth = (
            db.query(ProviderAuthSession)
            .filter(
                ProviderAuthSession.provider == claude_subscription.CLAUDE_SUBSCRIPTION_PROVIDER,
                ProviderAuthSession.owner == owner,
            )
            .first()
        )
        if auth is None:
            auth = ProviderAuthSession(
                id=str(uuid.uuid4())[:8],
                provider=claude_subscription.CLAUDE_SUBSCRIPTION_PROVIDER,
                owner=owner,
                label="Claude Subscription",
                base_url=base,
                auth_mode="claude",
            )
            db.add(auth)
        auth.base_url = base
        auth.access_token = access_token
        auth.refresh_token = refresh_token or ""
        auth.expires_at = expires_at
        auth.last_refresh = utcnow_naive()
        auth.auth_mode = "claude"

        ep = (
            db.query(ModelEndpoint)
            .filter(
                ModelEndpoint.base_url == base,
                ModelEndpoint.provider_auth_id == auth.id,
                ModelEndpoint.owner == owner,
            )
            .first()
        )
        if ep is None:
            ep = ModelEndpoint(
                id=str(uuid.uuid4())[:8],
                name="Claude Subscription",
                base_url=base,
                model_type="llm",
                endpoint_kind="api",
                owner=owner,
            )
            db.add(ep)
        ep.name = "Claude Subscription"
        ep.base_url = base
        ep.api_key = None
        ep.provider_auth_id = auth.id
        ep.is_enabled = True
        ep.supports_tools = True
        ep.model_type = "llm"
        ep.endpoint_kind = "api"
        ep.model_refresh_mode = "manual"
        ep.cached_models = json.dumps(models)
        db.commit()
        result = {"id": ep.id, "name": ep.name, "base_url": ep.base_url, "models": models}
    finally:
        db.close()

    try:
        from routes.model_routes import _invalidate_models_cache

        _invalidate_models_cache()
    except Exception:
        pass
    return result


def setup_claude_subscription_routes() -> APIRouter:
    router = APIRouter(prefix="/api/claude-subscription", tags=["claude-subscription"])

    @router.post("/complete")
    def complete(request: Request, token: str = Form(...)):
        require_admin(request)
        try:
            access, refresh, expires_at = claude_subscription.parse_pasted_credentials(token)
        except Exception as exc:
            raise claude_subscription.to_http_exception(exc)
        if not access:
            raise HTTPException(400, "No token found in the pasted value.")
        try:
            result = _provision_endpoint(access, refresh, expires_at, get_current_user(request) or None)
        except Exception as exc:
            logger.exception("Claude Subscription endpoint provisioning failed")
            raise claude_subscription.to_http_exception(exc)
        return {"status": "authorized", "endpoint": result}

    return router
