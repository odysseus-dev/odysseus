"""Product connection UX that redirects to 9router-hosted PKCE."""

import logging
from typing import Any, Dict, List, Mapping, Optional

from fastapi import HTTPException, Request

from routes.device_flow import (
    DeviceFlowPoll,
    DeviceFlowStart,
    PendingDeviceFlowStore,
    create_device_flow_router,
)
from src.auth_helpers import get_current_user
from src import chatgpt_subscription

logger = logging.getLogger(__name__)

_DEVICE_FLOW_STORE = PendingDeviceFlowStore()

# Spike against unmodified 9router 0.5.69 (do not vendor its source):
# /dashboard/providers hosts connect after /login; GET /api/oauth/{provider}/authorize
# accepts redirect_uri (default http://localhost:8080/callback) but requires a
# 9router dashboard session. Odysseus only redirects the browser there.
_NINEROUTER_PKCE_PATH = "/dashboard/providers"


def _provision_connection(projection: Dict[str, Any], owner: Optional[str]) -> Dict[str, Any]:
    return chatgpt_subscription.provision_connection(projection, owner)


def _provision_endpoint(_tokens: Dict, _owner: Optional[str]) -> Dict:
    raise ValueError("connection_id is required; Odysseus no longer stores provider tokens")


def _list_redacted_providers() -> List[Dict[str, Any]]:
    from services.ninerouter.metadata import NineRouterMetadataClient, redact_providers

    payload = NineRouterMetadataClient().get("/api/providers")
    return redact_providers(payload)


def _provider_rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        rows = payload.get("providers") or payload.get("data") or payload.get("connections") or []
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _match_pending_connection(providers: Any, pending: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    wanted = str(pending.get("connection_id") or "").strip()
    failed: Optional[Dict[str, Any]] = None
    for row in _provider_rows(providers):
        cid = str(row.get("id") or row.get("connectionId") or "").strip()
        if not cid:
            continue
        status = str(row.get("testStatus") or row.get("status") or "usable").lower()
        if status in {"error", "failed", "denied", "invalid"}:
            if wanted and cid == wanted:
                return {"id": cid, "status": "error", "error": str(row.get("error") or status)}
            failed = failed or {"id": cid, "status": "error", "error": str(row.get("error") or status)}
            continue
        if wanted and cid != wanted:
            continue
        return {
            "id": cid,
            "status": "usable",
            "entitlement": row.get("entitlement") or row.get("provider") or row.get("name"),
            "name": row.get("displayName") or row.get("name") or "9router",
        }
    if wanted and failed:
        return failed
    return None


def _start_device_flow(request: Request, _form) -> DeviceFlowStart:
    owner = get_current_user(request) or None
    redirect_url = f"{chatgpt_subscription.ninerouter_public_url()}{_NINEROUTER_PKCE_PATH}"
    return DeviceFlowStart(
        pending={"owner": owner, "redirect_url": redirect_url},
        response={
            "redirect_url": redirect_url,
            "verification_uri": redirect_url,
        },
        interval=5,
        expires_in=900,
    )


def _poll_device_flow(_request: Request, pending: Dict) -> DeviceFlowPoll:
    if pending.get("oauth_error"):
        return DeviceFlowPoll.failed(str(pending.get("oauth_error") or "denied"))

    try:
        providers = _list_redacted_providers()
    except Exception as exc:
        logger.debug("9router metadata poll failed: %s", exc)
        return DeviceFlowPoll.pending(str(exc))

    match = _match_pending_connection(providers, pending)
    if match is None:
        return DeviceFlowPoll.pending()
    if match.get("status") == "error":
        return DeviceFlowPoll.failed(str(match.get("error") or "denied"))

    result = _provision_connection(
        {
            "connection_id": match["id"],
            "status": "usable",
            "entitlement": match.get("entitlement"),
            "label": match.get("name") or "9router",
        },
        pending.get("owner"),
    )
    return DeviceFlowPoll.authorized(result)


def setup_chatgpt_subscription_routes():
    router = create_device_flow_router(
        prefix="/api/chatgpt-subscription",
        tags=["chatgpt-subscription"],
        store=_DEVICE_FLOW_STORE,
        start_flow=_start_device_flow,
        poll_flow=_poll_device_flow,
    )

    @router.get("/callback")
    def provider_oauth_callback(
        request: Request,
        connection_id: str = "",
        status: str = "usable",
        error: str = "",
        entitlement: str = "",
    ):
        params = request.query_params
        if params.get("access_token") or params.get("refresh_token") or params.get("code_verifier"):
            logger.warning("Discarded secret fields on 9router connection callback")
        if error:
            raise HTTPException(400, f"Provider connection failed: {error}")
        if not connection_id.strip():
            raise HTTPException(400, "Provider connection failed: missing connection id")
        owner = get_current_user(request) or None
        return _provision_connection(
            {
                "connection_id": connection_id.strip(),
                "status": status or "usable",
                "entitlement": entitlement or None,
                "label": "9router",
            },
            owner,
        )

    return router
