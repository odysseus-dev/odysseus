"""Read model metadata from Ollama without loading or generating with a model."""

from __future__ import annotations

from typing import Mapping
from urllib.parse import urlparse

import httpx

from src.constants import OLLAMA_METADATA_TIMEOUT_SECONDS


def ollama_metadata_base(endpoint_url: str) -> str | None:
    """Map native and default-port OpenAI-compatible URLs to the same API."""
    from src.llm_core import _is_ollama_native_url

    try:
        parsed = urlparse(endpoint_url)
        path = parsed.path.rstrip("/")
        if _is_ollama_native_url(endpoint_url):
            prefix = path.split("/api", 1)[0]
        elif parsed.port == 11434 and (path == "/v1" or path.startswith("/v1/")):
            prefix = ""
        else:
            return None
        return parsed._replace(path=prefix + "/api", params="", query="", fragment="").geturl()
    except (TypeError, ValueError):
        return None


def ollama_model_info(
    endpoint_url: str,
    model: str,
    headers: Mapping[str, str] | None = None,
) -> dict | None:
    base = ollama_metadata_base(endpoint_url)
    if not base or not model:
        return None
    try:
        response = httpx.post(
            base + "/show",
            json={"model": model},
            headers=headers,
            timeout=OLLAMA_METADATA_TIMEOUT_SECONDS,
        )
        if response.is_success:
            payload = response.json()
            return payload if isinstance(payload, dict) else None
    except (httpx.HTTPError, ValueError, TypeError):
        pass
    return None


def ollama_tool_support(
    endpoint_url: str,
    model: str,
    headers: Mapping[str, str] | None = None,
) -> bool | None:
    """None means the server did not report model-level tool capabilities."""
    info = ollama_model_info(endpoint_url, model, headers)
    capabilities = info.get("capabilities") if info is not None else None
    if not isinstance(capabilities, list) or not capabilities:
        return None
    if not all(isinstance(value, str) for value in capabilities):
        return None
    return "tools" in capabilities


def _positive_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def ollama_context_length(endpoint_url: str, model: str) -> int | None:
    """Prefer the loaded runner's context, then the model's configured limits."""
    base = ollama_metadata_base(endpoint_url)
    if not base:
        return None
    try:
        response = httpx.get(base + "/ps", timeout=OLLAMA_METADATA_TIMEOUT_SECONDS)
        if response.is_success:
            payload = response.json()
            models = payload.get("models") if isinstance(payload, dict) else None
            requested = model if ":" in model.rsplit("/", 1)[-1] else model + ":latest"
            for entry in models if isinstance(models, list) else ():
                if not isinstance(entry, dict):
                    continue
                if not any(entry.get(key) in {model, requested} for key in ("name", "model")):
                    continue
                context = _positive_int(entry.get("context_length"))
                if context:
                    return context
    except (httpx.HTTPError, ValueError, TypeError):
        pass

    info = ollama_model_info(endpoint_url, model)
    if info is None:
        return None
    # The existing provider reader parses PARAMETER num_ctx before the
    # architecture's *.context_length, including custom Modelfile aliases.
    from src.model_capability_readers.ollama import record_from_show_payload

    record = record_from_show_payload(model, info)
    return _positive_int(dict(record.capability.limits).get("context_tokens")) if record else None
