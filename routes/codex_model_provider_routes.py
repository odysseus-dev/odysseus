"""Admin-gated experimental Codex model-provider routes."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Any

from core.middleware import require_admin
from src.auth_helpers import get_current_user
from src.codex_model_provider import (
    CODEX_EXPERIMENTAL_MODEL_ID,
    CODEX_PROVIDER_ENDPOINT_ID,
    CODEX_PROVIDER_ENDPOINT_NAME,
    CODEX_PROVIDER_ENDPOINT_URL,
    CodexModelProvider,
)


class CodexTestChatRequest(BaseModel):
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
    async def add_model(request: Request, body: CodexTestChatRequest | None = None):
        """Add selected Codex CLI models to the normal Added Models list.

        Codex is backed by a virtual codex-cli:// endpoint, but we still store
        a ModelEndpoint row so the existing Added Models UI, checkbox manager,
        and session endpoint-id validation behave like Local/API providers.
        """
        require_admin(request)
        owner = get_current_user(request)
        status = await provider.status()
        models = status.get("models") or []
        model_ids = [m.get("id") if isinstance(m, dict) else str(m) for m in models]
        model_ids = [m for m in model_ids if m]
        if status.get("status") != "available" or not status.get("chat_supported") or not model_ids:
            raise HTTPException(400, status.get("message") or "Codex is not available. Sign in with Codex first.")

        requested = []
        if body:
            if body.models:
                requested.extend(str(m).strip() for m in body.models if str(m).strip())
            elif body.model:
                requested.append(body.model.strip())
        selected = [m for m in requested if m in model_ids]
        if not selected:
            selected = [model_ids[0]]

        # Preserve Codex CLI order, not checkbox click/order quirks.
        selected_set = set(selected)
        selected = [m for m in model_ids if m in selected_set]
        hidden = [m for m in model_ids if m not in selected_set]
        default_model = selected[0]

        import json
        from core.database import ModelEndpoint, SessionLocal
        from routes.model_routes import _load_settings, _save_settings

        db = SessionLocal()
        try:
            ep = db.query(ModelEndpoint).filter(ModelEndpoint.id == CODEX_PROVIDER_ENDPOINT_ID).first()
            if not ep:
                ep = ModelEndpoint(
                    id=CODEX_PROVIDER_ENDPOINT_ID,
                    name=CODEX_PROVIDER_ENDPOINT_NAME,
                    base_url=CODEX_PROVIDER_ENDPOINT_URL,
                    api_key=None,
                    is_enabled=True,
                    model_type="llm",
                    supports_tools=True,
                    owner=owner,
                )
                db.add(ep)
            ep.name = CODEX_PROVIDER_ENDPOINT_NAME
            ep.base_url = CODEX_PROVIDER_ENDPOINT_URL
            ep.api_key = None
            ep.is_enabled = True
            ep.model_type = "llm"
            ep.supports_tools = True
            ep.owner = owner
            ep.cached_models = json.dumps(model_ids)
            ep.hidden_models = json.dumps(hidden) if hidden else None
            db.commit()
        finally:
            db.close()

        settings = _load_settings()
        settings["default_endpoint_id"] = CODEX_PROVIDER_ENDPOINT_ID
        settings["default_model"] = default_model
        _save_settings(settings)

        endpoint = {
            "id": CODEX_PROVIDER_ENDPOINT_ID,
            "name": CODEX_PROVIDER_ENDPOINT_NAME,
            "base_url": CODEX_PROVIDER_ENDPOINT_URL,
            "models": selected,
            "online": True,
            "status": "online",
            "is_enabled": True,
            "model_type": "llm",
            "virtual": True,
        }
        return {"ok": True, "endpoint": endpoint, "model": default_model, "models": selected}

    @router.post("/test-chat")
    async def test_chat(request: Request, body: CodexTestChatRequest):
        require_admin(request)
        messages = body.messages or []
        if not messages and body.prompt:
            messages = [{"role": "user", "content": body.prompt}]
        if not messages:
            return {
                "ok": False,
                "status": "invalid_request",
                "error": "Provide either prompt or messages",
            }
        return await provider.test_chat(
            messages,
            model=body.model,
            timeout_seconds=body.timeout_seconds or 120,
        )

    return router
