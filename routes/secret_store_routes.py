"""Admin configuration routes for the internal secret store."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.middleware import require_admin
from src.integrations import load_integrations
from src.secrets_store import (
    LocalEncryptedSecretStore,
    OpenBaoSecretStore,
    SecretStoreConfigurationError,
    SecretStoreUnavailable,
    build_secret_store,
    configure_secret_store,
    load_secret_store_config,
    resolve_secret_store_config,
    save_secret_store_config,
)


class SecretStoreConfigRequest(BaseModel):
    enabled: bool = False
    integration_id: str = ""
    mount: str = "secret"
    prefix: str = "odysseus/internal"


def _openbao_integrations() -> list[dict]:
    result = []
    for integration in load_integrations():
        preset = str(integration.get("preset") or "").lower()
        name = str(integration.get("name") or "")
        if preset != "openbao" and name.lower() != "openbao":
            continue
        result.append(
            {
                "id": integration.get("id", ""),
                "name": name or "OpenBao",
                "base_url": integration.get("base_url", ""),
                "enabled": integration.get("enabled", True) is not False,
                "token_set": bool(integration.get("api_key")),
            }
        )
    return result


def _request_config(req: SecretStoreConfigRequest) -> dict[str, str]:
    return {
        "backend": "openbao" if req.enabled else "local",
        "integration_id": req.integration_id,
        "mount": req.mount,
        "prefix": req.prefix,
    }


def setup_secret_store_routes() -> APIRouter:
    router = APIRouter(prefix="/api/admin/secret-store", tags=["secret-store"])

    @router.get("")
    async def get_config(request: Request):
        require_admin(request)
        saved = load_secret_store_config()
        effective, overrides = resolve_secret_store_config()
        return {
            "saved": saved,
            "effective": effective,
            "enabled": effective["backend"] == "openbao",
            "environment_overrides": overrides,
            "integrations": _openbao_integrations(),
        }

    @router.post("/test")
    async def test_config(req: SecretStoreConfigRequest, request: Request):
        require_admin(request)
        try:
            store = build_secret_store(**_request_config(req))
            if isinstance(store, LocalEncryptedSecretStore):
                return {"ok": True, "backend": "local"}
            if not isinstance(store, OpenBaoSecretStore):
                raise SecretStoreConfigurationError("Unexpected secret-store backend")
            probe = store.probe()
            return {"ok": True, "backend": "openbao", **probe}
        except (SecretStoreConfigurationError, SecretStoreUnavailable, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("")
    async def save_config(req: SecretStoreConfigRequest, request: Request):
        require_admin(request)
        _, overrides = resolve_secret_store_config()
        if overrides:
            raise HTTPException(
                409,
                "Secret-store settings are controlled by environment variables: "
                + ", ".join(overrides),
            )
        proposed = _request_config(req)
        try:
            # Validate and connect before replacing a known-good saved config.
            store = build_secret_store(**proposed)
            if isinstance(store, OpenBaoSecretStore):
                store.probe()
            saved = save_secret_store_config(proposed)
            configure_secret_store(store)
        except (SecretStoreConfigurationError, SecretStoreUnavailable, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        effective, overrides = resolve_secret_store_config()
        return {
            "ok": True,
            "saved": saved,
            "effective": effective,
            "environment_overrides": overrides,
        }

    return router
