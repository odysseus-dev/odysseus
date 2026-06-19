"""Bootstrap a model endpoint from deployment environment variables."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

import httpx

from core.database import ModelEndpoint, SessionLocal, init_db, utcnow_naive
from src.settings import load_settings, save_settings

logger = logging.getLogger(__name__)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _get(name: str, default: str = "") -> str:
    return (os.getenv(name) or default or "").strip()


def _first(*names: str) -> str:
    for name in names:
        value = _get(name)
        if value:
            return value
    return ""


def _flag(name: str, default: bool = False) -> bool:
    value = _get(name).lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return default


def _clean_base(value: str) -> str:
    return (value or "").strip().rstrip("/")


def _looks_like_gateway(base: str) -> bool:
    value = (base or "").lower()
    return "mcp-server" in value or "ia-gateway" in value or ":3001" in value or ":3002" in value


def _should_run(base: str) -> bool:
    explicit = _get("ODYSSEUS_AUTO_REGISTER_MODEL_ENDPOINT")
    if explicit:
        return explicit.lower() in _TRUE
    if _get("ODYSSEUS_MODEL_ENDPOINT_BASE_URL") or _get("ODYSSEUS_GATEWAY_BASE_URL"):
        return True
    return _looks_like_gateway(base)


def _bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _model_ids(payload: Any) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()

    def add(raw: Any) -> None:
        model_id = str(raw or "").strip()
        if model_id and model_id not in seen:
            seen.add(model_id)
            ids.append(model_id)

    if not isinstance(payload, dict):
        return ids
    for item in payload.get("data") or []:
        if isinstance(item, dict):
            add(item.get("id") or item.get("name") or item.get("model"))
    for item in payload.get("models") or []:
        if isinstance(item, dict):
            add(item.get("id") or item.get("name") or item.get("model"))
    return ids


def _probe(base: str, token: str) -> list[str]:
    timeout = float(_get("ODYSSEUS_MODEL_ENDPOINT_PROBE_TIMEOUT", "30") or "30")
    response = httpx.get(f"{base}/models", headers=_bearer_headers(token), timeout=timeout)
    response.raise_for_status()
    return _model_ids(response.json())


def _stable_id(base: str) -> str:
    explicit = _get("ODYSSEUS_MODEL_ENDPOINT_ID")
    if explicit:
        return explicit
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]
    return f"env-{digest}"


def _pick(ids: list[str], *preferred: str) -> str:
    available = set(ids)
    for item in preferred:
        if item and item in available:
            return item
    return ids[0] if ids else ""


def _set_defaults(endpoint_id: str, ids: list[str]) -> None:
    if not ids or not _flag("ODYSSEUS_MODEL_ENDPOINT_SET_DEFAULT", True):
        return
    force = _flag("ODYSSEUS_MODEL_ENDPOINT_FORCE_DEFAULT", False)
    settings = load_settings()
    changed = False

    default_model = _get("ODYSSEUS_MODEL_ENDPOINT_DEFAULT_MODEL") or _pick(ids, "balanced", "fast", "ultra", "code")
    utility_model = _get("ODYSSEUS_MODEL_ENDPOINT_UTILITY_MODEL") or _pick(ids, "fast", "balanced", "economic", default_model)
    research_model = _get("ODYSSEUS_MODEL_ENDPOINT_RESEARCH_MODEL") or _pick(ids, "ultra", "balanced", default_model)
    task_model = _get("ODYSSEUS_MODEL_ENDPOINT_TASK_MODEL") or _pick(ids, "fast", "balanced", default_model)

    def put(endpoint_key: str, model_key: str, model_id: str) -> None:
        nonlocal changed
        if model_id and (force or not settings.get(endpoint_key)):
            settings[endpoint_key] = endpoint_id
            settings[model_key] = model_id
            changed = True

    put("default_endpoint_id", "default_model", default_model)
    put("utility_endpoint_id", "utility_model", utility_model)
    put("research_endpoint_id", "research_model", research_model)
    put("task_endpoint_id", "task_model", task_model)
    if changed:
        save_settings(settings)


def bootstrap_env_model_endpoint() -> bool:
    base = _clean_base(_first("ODYSSEUS_MODEL_ENDPOINT_BASE_URL", "ODYSSEUS_GATEWAY_BASE_URL", "OLLAMA_BASE_URL", "OLLAMA_URL"))
    if not base or not _should_run(base):
        return False

    token = _first("ODYSSEUS_MODEL_ENDPOINT_TOKEN", "ODYSSEUS_GATEWAY_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY")
    try:
        init_db()
        ids = _probe(base, token)
    except Exception as exc:
        logger.warning("Env model endpoint bootstrap skipped for %s: %s", base, exc)
        return False
    if not ids:
        logger.warning("Env model endpoint bootstrap found no models for %s", base)
        return False

    endpoint_id = _stable_id(base)
    now = utcnow_naive()
    name = _get("ODYSSEUS_MODEL_ENDPOINT_NAME") or ("IA Gateway" if _looks_like_gateway(base) else "Environment Model Endpoint")
    kind = _get("ODYSSEUS_MODEL_ENDPOINT_KIND") or ("proxy" if _looks_like_gateway(base) or token else "auto")
    refresh_mode = _get("ODYSSEUS_MODEL_ENDPOINT_REFRESH_MODE", "auto") or "auto"

    db = SessionLocal()
    try:
        endpoint = db.query(ModelEndpoint).filter(ModelEndpoint.id == endpoint_id).first()
        if endpoint is None:
            endpoint = db.query(ModelEndpoint).filter(ModelEndpoint.base_url == base).first()
        if endpoint is None:
            endpoint = ModelEndpoint(id=endpoint_id, created_at=now)
            db.add(endpoint)

        endpoint.name = name
        endpoint.base_url = base
        endpoint.api_key = token or None
        endpoint.is_enabled = True
        endpoint.cached_models = json.dumps(ids)
        endpoint.model_type = "llm"
        endpoint.endpoint_kind = kind
        endpoint.model_refresh_mode = refresh_mode
        endpoint.updated_at = now
        if _get("ODYSSEUS_MODEL_ENDPOINT_SUPPORTS_TOOLS") or _looks_like_gateway(base):
            endpoint.supports_tools = _flag("ODYSSEUS_MODEL_ENDPOINT_SUPPORTS_TOOLS", _looks_like_gateway(base))
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Failed to persist env model endpoint")
        return False
    finally:
        db.close()

    _set_defaults(endpoint_id, ids)
    logger.info("Bootstrapped env model endpoint %s with %s models", name, len(ids))
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("changed" if bootstrap_env_model_endpoint() else "skipped")
