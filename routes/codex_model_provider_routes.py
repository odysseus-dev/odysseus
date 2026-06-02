"""Admin-gated Codex CLI model-provider routes."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.middleware import require_admin
from src.auth_helpers import get_current_user
from src.codex_model_provider import (
    CODEX_PROVIDER_ENDPOINT_NAME,
    CODEX_PROVIDER_ENDPOINT_URL,
    CodexModelProvider,
    codex_endpoint_id_for_owner,
)
from src.settings import load_settings, save_settings


class CodexProviderRequest(BaseModel):
    prompt: str | None = None
    messages: list[dict[str, Any]] | None = None
    model: str | None = None
    models: list[str] | None = None
    timeout_seconds: int | None = None


def setup_codex_model_provider_routes(provider: CodexModelProvider | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/codex-model-provider", tags=["codex-model-provider"])
    provider = provider or CodexModelProvider()

    @router.get("/status")
    async def status(request: Request):
        require_admin(request)
        return await provider.status()

    @router.post("/add-model")
    async def add_model(request: Request, body: CodexProviderRequest | None = None):
        """Add the signed-in Codex CLI identity as an owner-scoped model endpoint."""
        require_admin(request)
        owner = get_current_user(request) or ""
        status = await provider.status()
        models = status.get("models") or []
        model_ids = [m.get("id") if isinstance(m, dict) else str(m) for m in models]
        model_ids = [m for m in model_ids if m]
        if status.get("status") != "available" or not status.get("chat_supported") or not model_ids:
            raise HTTPException(400, status.get("error") or "Codex is not available. Sign in with Codex first.")

        requested: list[str] = []
        if body:
            if body.models:
                requested.extend(str(m).strip() for m in body.models if str(m).strip())
            elif body.model:
                requested.append(body.model.strip())
        selected = [m for m in requested if m in model_ids]
        if not selected:
            selected = [model_ids[0]]

        selected_set = set(selected)
        selected = [m for m in model_ids if m in selected_set]
        hidden = [m for m in model_ids if m not in selected_set]
        default_model = selected[0]
        endpoint_id = codex_endpoint_id_for_owner(owner)

        from core.database import ModelEndpoint, SessionLocal

        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == endpoint_id).first()
            if not ep:
                ep = ModelEndpoint(
                    id=endpoint_id,
                    name=CODEX_PROVIDER_ENDPOINT_NAME,
                    base_url=CODEX_PROVIDER_ENDPOINT_URL,
                    api_key=None,
                    is_enabled=True,
                    model_type="llm",
                    supports_tools=False,
                    owner=owner or None,
                )
                db.add(ep)
            ep.name = CODEX_PROVIDER_ENDPOINT_NAME
            ep.base_url = CODEX_PROVIDER_ENDPOINT_URL
            ep.api_key = None
            ep.is_enabled = True
            ep.model_type = "llm"
            ep.supports_tools = False
            ep.owner = owner or None
            ep.cached_models = json.dumps(model_ids)
            ep.hidden_models = json.dumps(hidden) if hidden else None
            db.commit()
        finally:
            db.close()

        settings = load_settings()
        if not settings.get("default_endpoint_id"):
            settings["default_endpoint_id"] = endpoint_id
            settings["default_model"] = default_model
            save_settings(settings)

        endpoint = {
            "id": endpoint_id,
            "name": CODEX_PROVIDER_ENDPOINT_NAME,
            "base_url": CODEX_PROVIDER_ENDPOINT_URL,
            "models": selected,
            "online": True,
            "status": "online",
            "is_enabled": True,
            "model_type": "llm",
            "supports_tools": False,
            "is_subscription": True,
            "experimental": True,
            "virtual": True,
        }
        return {"ok": True, "endpoint": endpoint, "model": default_model, "models": selected}

    @router.post("/test-chat")
    async def test_chat(request: Request, body: CodexProviderRequest):
        require_admin(request)
        messages = body.messages or []
        if not messages and body.prompt:
            messages = [{"role": "user", "content": body.prompt}]
        if not messages:
            return {"ok": False, "status": "invalid_request", "error": "Provide either prompt or messages"}
        return await provider.test_chat(
            messages,
            model=body.model,
            timeout_seconds=body.timeout_seconds or 120,
        )

    return router
