"""ChatGPT Subscription device-flow; overlay 9router holds the token.

Agents: start OpenAI device-code (user_code + /codex/device). Do not call
9router ``/api/oauth/codex/authorize`` — that opens /authorize with an
Odysseus redirect_uri and OpenAI returns unknown_error. Poll until
access_token, then ``import_codex_token`` into unpublished 9router.
Odysseus stores only the opaque connection projection. Never dashboard.
"""

import logging
from typing import Any, Dict, List, Mapping, Optional

from fastapi import HTTPException, Request

from routes.device_flow import (
    DeviceFlowPoll,
    DeviceFlowStart,
    PendingDeviceFlowStore,
    create_device_flow_router,
)
from services.ninerouter.connect import NineRouterConnectClient, NineRouterConnectError
from src.auth_helpers import get_current_user
from src import chatgpt_subscription

logger = logging.getLogger(__name__)

_DEVICE_FLOW_STORE = PendingDeviceFlowStore()

# Odysseus browser callback (IdP redirect_uri). Must not be 9router dashboard.
_OAUTH_CALLBACK_PATH = "/api/ninerouter/connections/oauth/callback"


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
    """Start ChatGPT device-code; return /codex/device + user_code, never /authorize."""
    owner = get_current_user(request) or None
    try:
        data = chatgpt_subscription.request_device_code()
    except chatgpt_subscription.ChatGPTSubscriptionError as exc:
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"ChatGPT device-code request failed: {exc}") from exc
    device_auth_id = str(data.get("device_auth_id") or "").strip()
    user_code = str(data.get("user_code") or "").strip()
    if not device_auth_id or not user_code:
        raise HTTPException(502, "ChatGPT device-code response was missing required fields.")
    verification_uri = str(
        data.get("verification_uri") or f"{chatgpt_subscription.CHATGPT_OAUTH_ISSUER}/codex/device"
    )
    return DeviceFlowStart(
        pending={
            "owner": owner,
            "device_auth_id": device_auth_id,
            "user_code": user_code,
        },
        response={
            "user_code": user_code,
            "verification_uri": verification_uri,
        },
        interval=int(data.get("interval") or 5),
        expires_in=int(data.get("expires_in") or 900),
    )


def _poll_device_flow(_request: Request, pending: Dict) -> DeviceFlowPoll:
    if pending.get("oauth_error"):
        return DeviceFlowPoll.failed(str(pending.get("oauth_error") or "denied"))

    device_auth_id = str(pending.get("device_auth_id") or "").strip()
    user_code = str(pending.get("user_code") or "").strip()
    if device_auth_id and user_code:
        try:
            data = chatgpt_subscription.poll_device_auth(device_auth_id, user_code)
        except Exception as exc:
            logger.debug("ChatGPT device poll failed: %s", exc)
            return DeviceFlowPoll.pending(str(exc))
        if not isinstance(data, dict):
            return DeviceFlowPoll.pending()
        err = str(data.get("error") or "")
        if err in {"authorization_pending", "slow_down"} or str(data.get("status") or "") == "pending":
            return DeviceFlowPoll.pending()
        token = str(data.get("access_token") or data.get("accessToken") or "").strip()
        if not token:
            code = str(data.get("authorization_code") or data.get("code") or "").strip()
            # OpenAI deviceauth/token returns the PKCE verifier with the code.
            # Homemade verifiers 400 on oauth/token.
            verifier = str(
                data.get("code_verifier")
                or data.get("codeVerifier")
                or pending.get("code_verifier")
                or ""
            ).strip()
            if code and verifier:
                try:
                    exchanged = chatgpt_subscription.exchange_authorization_code(code, verifier)
                except Exception as exc:
                    logger.warning(
                        "ChatGPT authorization_code exchange failed: %s",
                        str(exc).split(":")[0][:120],
                    )
                    return DeviceFlowPoll.failed("ChatGPT token exchange failed")
                if isinstance(exchanged, dict):
                    token = str(
                        exchanged.get("access_token") or exchanged.get("accessToken") or ""
                    ).strip()
            if not token:
                logger.info(
                    "ChatGPT device poll 200 without access_token keys=%s",
                    sorted(str(k) for k in data.keys()),
                )
                return DeviceFlowPoll.pending()
        try:
            row = NineRouterConnectClient().import_codex_token(token)
        except NineRouterConnectError as exc:
            return DeviceFlowPoll.failed(str(exc))
        cid = str(row.get("id") or row.get("connection_id") or "").strip()
        if not cid:
            return DeviceFlowPoll.failed("9router import did not return a connection id")
        result = _provision_connection(
            {
                "connection_id": cid,
                "status": "usable",
                "entitlement": row.get("entitlement") or row.get("provider") or "codex",
                "label": row.get("name") or row.get("label") or "9router",
            },
            pending.get("owner"),
        )
        return DeviceFlowPoll.authorized(result)

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
