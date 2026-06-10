# routes/copilot_routes.py
"""GitHub Copilot device-flow login.

Drives the GitHub OAuth *device flow* and, on success, creates (or refreshes)
an owner-scoped ``ModelEndpoint`` pointing at the Copilot API with the
device-flow access token stored as its (encrypted) ``api_key``. After that the
endpoint behaves like any other OpenAI-compatible provider — the Copilot-
specific request headers are injected centrally by ``build_headers`` /
``_provider_headers`` (see :mod:`src.copilot`).

Flow:
  1. ``POST /api/copilot/device/start`` → returns a ``poll_id`` plus the
     ``user_code`` + ``verification_uri`` to show the user. The secret
     ``device_code`` is kept server-side, never sent to the browser.
  2. The browser polls ``POST /api/copilot/device/poll`` with ``poll_id``.
     While pending it returns ``{status: "pending"}``; once the user authorises
     it provisions the endpoint and returns ``{status: "authorized", ...}``.

All routes are admin-gated (endpoint/provider management is an admin action).
"""

import json
import uuid
import logging
from typing import Dict, Optional

import httpx
from fastapi import HTTPException, Request

from core.database import SessionLocal, ModelEndpoint
from routes.device_flow import (
    DeviceFlowPoll,
    DeviceFlowStart,
    PendingDeviceFlowStore,
    create_device_flow_router,
)
from src.auth_helpers import get_current_user
from src import copilot

logger = logging.getLogger(__name__)

_DEVICE_FLOW_STORE = PendingDeviceFlowStore()


def _provision_endpoint(token: str, base: str, owner: Optional[str]) -> Dict:
    """Create or update the owner's Copilot endpoint with a fresh token."""
    try:
        models = copilot.fetch_models(base, token)
    except Exception as e:
        logger.warning(f"Copilot model fetch failed during provisioning: {e}")
        models = []
    model_ids = [m["id"] for m in models]
    # Copilot picker models support OpenAI-style tool calling; mark the endpoint
    # tool-capable so the agent loop sends native tool schemas.
    # Tool-capable if any picker model advertises tool_calls. When the model
    # fetch failed (empty list) default to True, since Copilot picker models
    # support OpenAI-style tool calling.
    supports_tools = bool(not models or any(m.get("tool_calls") for m in models))

    db = SessionLocal()
    try:
        ep = (
            db.query(ModelEndpoint)
            .filter(ModelEndpoint.base_url == base)
            .filter((ModelEndpoint.owner.is_(None)) | (ModelEndpoint.owner == owner))
            .order_by(ModelEndpoint.owner.desc())
            .first()
        )
        if ep is None:
            ep = ModelEndpoint(
                id=str(uuid.uuid4())[:8],
                name="GitHub Copilot",
                base_url=base,
                model_type="llm",
                owner=owner,
            )
            db.add(ep)
        ep.api_key = token
        ep.is_enabled = True
        ep.supports_tools = supports_tools
        if model_ids:
            ep.cached_models = json.dumps(model_ids)
        db.commit()
        result = {
            "id": ep.id,
            "name": ep.name,
            "base_url": ep.base_url,
            "models": model_ids,
        }
    finally:
        db.close()

    # Best-effort: refresh the model cache so the new endpoint shows up.
    try:
        from routes.model_routes import _invalidate_models_cache
        _invalidate_models_cache()
    except Exception:
        pass
    return result


def _start_device_flow(request: Request, form) -> DeviceFlowStart:
    host = copilot.GITHUB_HOST
    ent = str(form.get("enterprise_url") or "").strip()
    if ent:
        host = copilot.normalize_domain(ent)
    try:
        data = copilot.request_device_code(host)
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        raise HTTPException(502, f"GitHub device-code request failed (HTTP {status})")
    except Exception as e:
        raise HTTPException(502, f"GitHub device-code request failed: {e}")

    device_code = data.get("device_code")
    if not device_code:
        raise HTTPException(502, "GitHub did not return a device code")

    # verification_uri_complete embeds the user code, so the browser tab we
    # open lands the user straight on GitHub's "Authorize" screen with the
    # code pre-filled — one click, no manual code entry.
    return DeviceFlowStart(
        pending={
            "device_code": device_code,
            "host": host,
            "enterprise_url": ent,
            "owner": get_current_user(request) or None,
        },
        response={
            "user_code": data.get("user_code"),
            "verification_uri": data.get("verification_uri"),
            "verification_uri_complete": data.get("verification_uri_complete"),
        },
        interval=int(data.get("interval") or 5),
        expires_in=int(data.get("expires_in") or 900),
    )


def _poll_device_flow(_request: Request, pending: Dict) -> DeviceFlowPoll:
    try:
        data = copilot.poll_access_token(pending["host"], pending["device_code"])
    except Exception as e:
        return DeviceFlowPoll.pending(f"poll error: {e}")

    token = data.get("access_token")
    if token:
        base = copilot.enterprise_base(pending["enterprise_url"]) if pending["enterprise_url"] else copilot.COPILOT_BASE
        try:
            result = _provision_endpoint(token, base, pending["owner"])
        except Exception as e:
            logger.exception("Copilot endpoint provisioning failed")
            raise HTTPException(500, f"Login succeeded but provisioning failed: {e}")
        return DeviceFlowPoll.authorized(result)

    err = data.get("error")
    if err == "authorization_pending":
        return DeviceFlowPoll.pending()
    if err == "slow_down":
        return DeviceFlowPoll.slow_down(int(data.get("interval") or 0) or None)
    if err in ("expired_token", "access_denied"):
        return DeviceFlowPoll.failed(err)
    # Unknown error — surface but keep the session for another try.
    return DeviceFlowPoll.pending(err or "unknown")


def setup_copilot_routes():
    return create_device_flow_router(
        prefix="/api/copilot",
        tags=["copilot"],
        store=_DEVICE_FLOW_STORE,
        start_flow=_start_device_flow,
        poll_flow=_poll_device_flow,
    )
