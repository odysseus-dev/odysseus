"""Discover models exposed by a local LM Studio server for hwfit ranking."""

from __future__ import annotations

import os
import re
import time
from urllib.parse import urlparse

import httpx

from services.hwfit.models import infer_quantization_from_name


_CACHE_TTL = 30.0
_lmstudio_cache: dict[str, tuple[list[dict], float]] = {}
_PARAM_RE = re.compile(r"(?i)(?:^|[-_.])(\d+(?:\.\d+)?)\s*([BM])(?:[-_.]|$)")
_CONTEXT_BY_ARCH = {
    "gemma": 8192,
    "llama": 4096,
    "qwen": 32768,
    "mistral": 32768,
    "mixtral": 32768,
    "deepseek": 65536,
    "phi": 4096,
}


def fetch_lmstudio_models(host: str = "") -> list[dict]:
    """Query LM Studio's native API and return hwfit-compatible model dicts."""
    cache_key = host or "local"
    now = time.monotonic()
    cached = _lmstudio_cache.get(cache_key)
    if cached and cached[1] > now:
        return list(cached[0])

    models: list[dict] = []
    seen: set[str] = set()
    for url in _candidate_urls(host):
        try:
            response = httpx.get(url, timeout=1.5)
            data = response.json() if response.is_success else {}
        except Exception:
            continue
        raw_models = data.get("models")
        if not _looks_like_lmstudio(raw_models):
            continue
        for item in raw_models:
            entry = _entry_from_lmstudio_item(item)
            if not entry or entry["name"] in seen:
                continue
            seen.add(entry["name"])
            models.append(entry)
        break

    _lmstudio_cache[cache_key] = (models, now + _CACHE_TTL)
    return list(models)


def invalidate_cache() -> None:
    """Clear the LM Studio catalog cache."""
    _lmstudio_cache.clear()


def _candidate_urls(host: str) -> list[str]:
    """Return native LM Studio model-list URLs to try."""
    if host:
        return [f"http://{host}:1234/api/v1/models"]

    urls: list[str] = []
    env_url = (os.environ.get("LM_STUDIO_URL") or "").strip()
    if env_url:
        parsed = urlparse(env_url if "://" in env_url else "http://" + env_url)
        if parsed.hostname:
            scheme = parsed.scheme or "http"
            port = parsed.port or 1234
            urls.append(f"{scheme}://{parsed.hostname}:{port}/api/v1/models")
    urls.extend([
        "http://127.0.0.1:1234/api/v1/models",
        "http://host.docker.internal:1234/api/v1/models",
    ])
    return list(dict.fromkeys(urls))


def _looks_like_lmstudio(models: object) -> bool:
    """Return True when a model list has LM Studio's native item shape."""
    return (
        isinstance(models, list)
        and bool(models)
        and isinstance(models[0], dict)
        and "key" in models[0]
        and "architecture" in models[0]
    )


def _entry_from_lmstudio_item(item: dict) -> dict | None:
    """Convert one LM Studio native model item to a hwfit catalog entry."""
    key = str(item.get("key") or item.get("id") or "").strip()
    if not key:
        return None
    display_name = str(item.get("display_name") or key).strip()
    architecture = str(item.get("architecture") or "").strip()
    fmt = str(item.get("format") or "").lower()
    quant = _quantization_name(item) or infer_quantization_from_name(key) or "Q4_K_M"
    parameter_count = _parameter_count(item, key)
    context = _context_length(item, architecture, key)
    is_gguf = fmt == "gguf" or quant.upper().startswith(("Q", "IQ"))
    capabilities = _capabilities(item)

    entry = {
        "name": display_name,
        "provider": "LM Studio",
        "parameter_count": parameter_count,
        "quantization": quant,
        "quant": quant,
        "architecture": architecture,
        "format": fmt,
        "context_length": context,
        "context": context,
        "backend": "lmstudio",
        "_source": "lmstudio",
        "source": "lmstudio",
        "capabilities": capabilities,
        "lmstudio_key": key,
    }
    if is_gguf:
        entry["is_gguf"] = True
        entry["gguf_sources"] = [{"repo": key, "kind": "GGUF"}]
    else:
        entry["gguf_sources"] = []
    return entry


def _quantization_name(item: dict) -> str:
    """Return LM Studio's quantization label when present."""
    quant = item.get("quantization")
    if isinstance(quant, dict):
        return str(quant.get("name") or "").upper()
    if isinstance(quant, str):
        return quant.upper()
    return ""


def _parameter_count(item: dict, key: str) -> str:
    """Infer parameter count from LM Studio metadata or model key."""
    for field in ("parameter_count", "params"):
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
        if isinstance(value, (int, float)) and value > 0:
            if value > 1_000_000:
                return f"{value / 1_000_000_000:g}B"
    value = item.get("size")
    if isinstance(value, str) and value.strip():
        return value.strip().upper()
    match = _PARAM_RE.search(key)
    if match:
        return f"{float(match.group(1)):g}{match.group(2).upper()}"
    return ""


def _context_length(item: dict, architecture: str, key: str) -> int:
    """Return context length from LM Studio metadata or a family default."""
    for field in ("context_length", "max_context_length", "trained_context_length", "n_ctx_train"):
        value = item.get(field)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    haystack = f"{architecture} {key}".lower()
    for family, ctx in _CONTEXT_BY_ARCH.items():
        if family in haystack:
            return ctx
    return 4096


def _capabilities(item: dict) -> list[str]:
    """Normalize LM Studio capability flags to Odysseus catalog labels."""
    caps = item.get("capabilities")
    if not isinstance(caps, dict):
        return []
    out = []
    if caps.get("vision"):
        out.append("vision")
    if caps.get("tools") or caps.get("tool_use"):
        out.append("tools")
    return out
