"""Claude Subscription OAuth setup routes (authorization-code + PKCE).

Unlike the ChatGPT provider (device-poll), Claude's subscription login is an
authorization-code flow: the user opens an authorize URL, approves, and pastes
the returned ``code`` (optionally ``code#state``) back. So this exposes:

    POST /api/claude-subscription/start     -> { authorize_url, login_id, expires_in }
    POST /api/claude-subscription/complete  -> { status: "connected", endpoint }
    POST /api/claude-subscription/cancel    -> { status: "cancelled" }

The pending PKCE verifier + state live in the shared in-memory
``PendingDeviceFlowStore`` (never persisted, never sent to the client). All
routes are admin-gated, matching the ChatGPT provider.

Register in app.py:  app.include_router(setup_claude_subscription_routes())
"""

import json
import logging
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, Form, HTTPException, Request

from core.database import ModelEndpoint, ProviderAuthSession, SessionLocal, utcnow_naive
from core.middleware import require_admin
from routes.device_flow import PendingDeviceFlowStore
from src.auth_helpers import get_current_user
from src import claude_subscription

logger = logging.getLogger(__name__)

_PENDING_STORE = PendingDeviceFlowStore()


def _provision_endpoint(tokens: Dict, owner: Optional[str]) -> Dict:
    """Persist the OAuth credentials and (re)create the model endpoint.

    Mirrors ``chatgpt_subscription_routes._provision_endpoint`` but:
      * base_url is api.anthropic.com so the existing Anthropic chat path is used,
      * ``supports_tools=False`` (first cut, like the ChatGPT provider — see below),
      * ``auth_mode="claude"`` marks the session for the runtime resolver.
    """
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not access_token:
        raise ValueError("Claude token response was missing access_token")
    if not refresh_token:
        # Without a refresh token the connection dies at first expiry. Allow it
        # but warn — some flows only return a refresh token on first consent.
        logger.warning("Claude Subscription connected without a refresh token; will require reconnect at expiry")

    base = claude_subscription.DEFAULT_CLAUDE_SUBSCRIPTION_BASE_URL
    models = claude_subscription.fetch_available_models(access_token) or list(
        claude_subscription.DEFAULT_CLAUDE_MODELS
    )

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
        if refresh_token:
            auth.refresh_token = refresh_token
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
        # Tools off for the first cut, mirroring the ChatGPT subscription provider.
        # Claude Code OAuth tool use needs `cc_` tool-name prefixing (request +
        # response) which isn't wired yet; enable once that lands and is tested.
        ep.supports_tools = False
        ep.model_type = "llm"
        ep.endpoint_kind = "api"
        ep.model_refresh_mode = "manual"
        ep.cached_models = json.dumps(models)
        db.commit()
        result = {
            "id": ep.id,
            "name": ep.name,
            "base_url": ep.base_url,
            "models": models,
        }
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

    @router.post("/start")
    async def start(request: Request):
        require_admin(request)
        try:
            code_verifier, code_challenge = claude_subscription.generate_pkce_pair()
            # Claude Code's flow reuses the PKCE verifier as the `state` value;
            # the callback returns `code#state`, so this is echoed back to verify.
            state = code_verifier
            authorize_url = claude_subscription.build_authorize_url(state, code_challenge)
        except Exception as exc:
            raise claude_subscription.to_http_exception(exc)

        login_id = _PENDING_STORE.add(
            {
                "code_verifier": code_verifier,
                "state": state,
                "owner": get_current_user(request) or None,
            },
            interval=5,
            expires_in=900,
        )
        return {"authorize_url": authorize_url, "login_id": login_id, "expires_in": 900}

    @router.post("/complete")
    async def complete(request: Request, login_id: str = Form(...), code: str = Form(...)):
        require_admin(request)
        payload = _PENDING_STORE.get_payload(login_id)
        if payload is None:
            raise HTTPException(404, "Unknown or expired login session")

        authorization_code, returned_state = claude_subscription.parse_authorization_code(code)
        if not authorization_code:
            raise HTTPException(400, "No authorization code provided")
        expected_state = payload.get("state")
        if returned_state and expected_state and returned_state != expected_state:
            _PENDING_STORE.pop(login_id)
            raise HTTPException(400, "Authorization state mismatch; restart the login")

        try:
            tokens = claude_subscription.exchange_authorization_code(
                authorization_code,
                payload.get("code_verifier") or "",
                state=expected_state,
            )
            result = _provision_endpoint(tokens, payload.get("owner"))
        except Exception as exc:
            logger.exception("Claude Subscription endpoint provisioning failed")
            raise claude_subscription.to_http_exception(exc)
        finally:
            _PENDING_STORE.pop(login_id)

        return {"status": "connected", "endpoint": result}

    @router.post("/cancel")
    def cancel(request: Request, login_id: str = Form(...)):
        require_admin(request)
        _PENDING_STORE.pop(login_id)
        return {"status": "cancelled"}

    return router