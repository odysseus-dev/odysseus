"""Codex CLI model-provider capability surface.

This layer builds on ``src.codex_auth``. It does not read Codex tokens or
pretend Codex is an OpenAI-compatible HTTP endpoint. Plain chat execution is
intentionally left for a follow-up slice.
"""

from __future__ import annotations

import asyncio
import os
import threading
from typing import Any, Awaitable

from src.codex_auth import get_codex_auth_service


PROVIDER_TYPE = "codex_cli"
AUTH_TYPE = "codex_cli"
FEATURE_FLAG = "ODYSSEUS_CODEX_MODEL_PROVIDER_ENABLED"
INTERNAL_URL_PREFIX = "odysseus://codex-cli/"
DEFAULT_ENDPOINT_ID = "codex-cli"
DEFAULT_MODEL_ID = "codex-cli/chatgpt-experimental"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def feature_enabled() -> bool:
    return _truthy(os.getenv(FEATURE_FLAG, "false"))


def internal_url_for_endpoint(endpoint_id: str | None = None) -> str:
    endpoint_id = (endpoint_id or DEFAULT_ENDPOINT_ID).strip() or DEFAULT_ENDPOINT_ID
    return INTERNAL_URL_PREFIX + endpoint_id


def is_internal_url(url: str | None) -> bool:
    return (url or "").strip().startswith(INTERNAL_URL_PREFIX)


def endpoint_id_from_internal_url(url: str | None) -> str:
    value = (url or "").strip()
    if not value.startswith(INTERNAL_URL_PREFIX):
        return ""
    return value[len(INTERNAL_URL_PREFIX):].strip().strip("/")


def _run_sync(coro: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    """Run an async auth probe from sync model routes."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    box: dict[str, Any] = {}

    def _runner() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover - defensive bridge
            box["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value") or {}


def _redacted_auth_status(status: dict[str, Any]) -> dict[str, Any]:
    blocked_fragments = ("token", "secret", "authorization", "bearer")
    redacted: dict[str, Any] = {}
    for key, value in (status or {}).items():
        key_s = str(key)
        if any(fragment in key_s.lower() for fragment in blocked_fragments):
            continue
        if isinstance(value, str) and any(fragment in value.lower() for fragment in blocked_fragments):
            value = "[redacted]"
        redacted[key_s] = value
    return redacted


def provider_status_from_auth(auth_status: dict[str, Any]) -> dict[str, Any]:
    auth_status = _redacted_auth_status(auth_status)
    enabled = feature_enabled()
    cli_available = bool(auth_status.get("codex_cli_available"))
    authenticated = bool(auth_status.get("codex_authenticated") or auth_status.get("authenticated"))
    endpoint_id = DEFAULT_ENDPOINT_ID

    if not enabled:
        status = "disabled"
        message = "Codex model provider is disabled"
    elif not cli_available:
        status = auth_status.get("status") or "cli_unavailable"
        message = auth_status.get("message") or "Codex CLI is unavailable"
    elif not authenticated:
        status = "sign_in_required"
        message = auth_status.get("message") or "Sign in with Codex before enabling model access"
    else:
        status = "available"
        message = "Codex CLI authentication is available"

    models = [DEFAULT_MODEL_ID] if status == "available" else []
    return {
        "status": status,
        "message": message,
        "enabled": enabled,
        "provider_type": PROVIDER_TYPE,
        "auth_type": AUTH_TYPE,
        "endpoint_id": endpoint_id,
        "endpoint_url": internal_url_for_endpoint(endpoint_id),
        "models": models,
        "requires_sign_in": enabled and cli_available and not authenticated,
        "auth": auth_status,
        "capabilities": {
            "chat_supported": False,
            "streaming_supported": False,
            "agent_tools_supported": False,
            "session_resume_supported": False,
        },
    }


async def provider_status() -> dict[str, Any]:
    auth_status = await get_codex_auth_service().status()
    return provider_status_from_auth(auth_status)


def provider_status_sync() -> dict[str, Any]:
    return provider_status_from_auth(_run_sync(get_codex_auth_service().status()))


def codex_model_list_item_if_available() -> dict[str, Any] | None:
    if not feature_enabled():
        return None
    status = provider_status_sync()
    if status.get("status") != "available":
        return None
    models = [str(model) for model in (status.get("models") or []) if str(model or "").strip()]
    if not models:
        return None
    return {
        "host": "custom",
        "port": 0,
        "url": status["endpoint_url"],
        "models": models,
        "models_display": [model.split("/")[-1] for model in models],
        "models_extra": [],
        "models_extra_display": [],
        "endpoint_id": status["endpoint_id"],
        "endpoint_name": "Codex / ChatGPT",
        "category": "api",
        "model_type": "llm",
        "provider_type": PROVIDER_TYPE,
        "auth_type": AUTH_TYPE,
        "offline": True,
        "disabled_reason": "Codex model provider chat execution is not enabled in this slice",
        "capabilities": status["capabilities"],
    }
