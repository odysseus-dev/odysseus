"""Odysseus BFF for in-stack 9router provider connections.

Agents: these routes are the product surface. Odysseus never stores API keys
or OAuth tokens; 9router holds secrets. Persistence is only
``chatgpt_subscription.provision_connection`` projections
(``connection_id`` / ``status`` / ``entitlement`` / ``label`` / ``owner``).

Do not POST ``ModelEndpoint`` here. Do not open ``/dashboard/providers``.
Do not call host-wide 9router. Inject ``NineRouterConnectClient`` in tests.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Form, HTTPException, Request

from src.auth_helpers import get_current_user
from src import chatgpt_subscription
from services.ninerouter.connect import NineRouterConnectClient, NineRouterConnectError

logger = logging.getLogger(__name__)

# Browser callback path (IdP redirect_uri). Must stay under Odysseus, not 9router.
_OAUTH_CALLBACK_PATH = "/api/ninerouter/connections/oauth/callback"


def _owner(request: Request) -> Optional[str]:
    """Return the authenticated Odysseus user, or None when unauthenticated."""
    return get_current_user(request) or None


def _oauth_redirect_uri(request: Request) -> str:
    """Build the Odysseus OAuth callback URL from the incoming request origin."""
    return str(request.base_url).rstrip("/") + _OAUTH_CALLBACK_PATH


def _connection_id_from_row(row: Any) -> str:
    """Extract opaque 9router connection id from a redacted connect payload."""
    if not isinstance(row, dict):
        return ""
    return str(row.get("connection_id") or row.get("id") or row.get("connectionId") or "").strip()


def _provision_row(row: dict[str, Any], owner: Optional[str]) -> dict[str, Any]:
    """Persist a projection only. Raises HTTP 400 if connection_id is missing."""
    connection_id = _connection_id_from_row(row)
    if not connection_id:
        raise HTTPException(400, "Provider connection failed: missing connection id")
    try:
        return chatgpt_subscription.provision_connection(
            {
                "connection_id": connection_id,
                "status": str(row.get("status") or "usable"),
                "entitlement": row.get("entitlement") or row.get("provider") or row.get("name"),
                "label": row.get("label") or row.get("name") or row.get("provider") or "9router",
            },
            owner,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def setup_ninerouter_connection_routes() -> APIRouter:
    """Register catalog, API-key connect, OAuth start/callback, and disconnect."""
    router = APIRouter(prefix="/api/ninerouter/connections", tags=["ninerouter-connections"])

    @router.get("")
    def list_connections():
        """Redacted catalog. Fail closed when overlay 9router is unreachable."""
        try:
            providers = NineRouterConnectClient().list_providers()
        except NineRouterConnectError as exc:
            logger.warning("9router connect catalog failed: %s", exc)
            return {"ok": False, "providers": [], "error": str(exc)}
        return {"ok": True, "providers": providers, "error": None}

    @router.post("")
    def create_api_key_connection(
        request: Request,
        provider: str = Form(...),
        api_key: str = Form(...),
    ):
        """POST the key once to 9router; store only the opaque projection."""
        try:
            row = NineRouterConnectClient().create_api_key(provider, api_key)
        except NineRouterConnectError as exc:
            raise HTTPException(502, str(exc)) from exc
        return _provision_row(row if isinstance(row, dict) else {}, _owner(request))

    @router.post("/oauth/start")
    def oauth_start(request: Request, provider: str = Form(...)):
        """Return an upstream IdP authorization_url (never 9router dashboard)."""
        try:
            started = NineRouterConnectClient().start_oauth(provider, _oauth_redirect_uri(request))
        except NineRouterConnectError as exc:
            raise HTTPException(502, str(exc)) from exc
        url = (started or {}).get("authorization_url") if isinstance(started, dict) else ""
        out: dict[str, Any] = {"authorization_url": url}
        if isinstance(started, dict) and started.get("poll_id"):
            out["poll_id"] = started["poll_id"]
        if isinstance(started, dict) and started.get("state"):
            out["state"] = started["state"]
        return out

    @router.get("/oauth/callback")
    def oauth_callback(
        request: Request,
        code: str = "",
        state: str = "",
        error: str = "",
        connection_id: str = "",
        provider: str = "",
    ):
        """Complete OAuth at 9router. Ignore token query params; never persist them."""
        params = request.query_params
        if params.get("access_token") or params.get("refresh_token") or params.get("code_verifier"):
            logger.warning("Discarded secret fields on 9router connection callback")
        if error:
            raise HTTPException(400, f"Provider connection failed: {error}")

        owner = _owner(request)
        cid = (connection_id or "").strip()

        # Authorization code → 9router exchange, then project the returned id.
        if (code or "").strip():
            try:
                row = NineRouterConnectClient().complete_oauth(
                    provider or "codex",
                    code.strip(),
                    state=state or None,
                )
            except NineRouterConnectError as exc:
                raise HTTPException(502, str(exc)) from exc
            return _provision_row(row if isinstance(row, dict) else {}, owner)

        # 9router already handed back a connection_id (no code on this hop).
        if cid:
            return _provision_row({"connection_id": cid, "status": "usable"}, owner)

        raise HTTPException(400, "Provider connection failed: missing connection id")

    @router.delete("/{connection_id}")
    def delete_connection(connection_id: str):
        """Remove the connection in overlay 9router (secrets live there)."""
        try:
            NineRouterConnectClient().delete_connection(connection_id)
        except NineRouterConnectError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"ok": True}

    return router
