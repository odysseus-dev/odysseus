"""Discover models installed in a local Ollama instance and return
hwfit-compatible catalog entries for ranking in the What Fits? tab."""

import json
import re
import time
import urllib.request

_CACHE_TTL = 30  # seconds — avoids hammering Ollama on every scan request
_ollama_cache: list | None = None
_ollama_cache_ts: float = 0.0

# Context defaults derived from well-known model families.
# Ollama's /api/tags does not expose context length, so we use
# conservative known-good defaults rather than guessing a huge value.
_CONTEXT_BY_FAMILY = {
    "gemma3": 131072,
    "llama3": 131072,
    "qwen3": 32768,
    "qwen2": 32768,
    "gemma2": 8192,
    "gemma": 8192,
    "mistral": 32768,
    "phi": 4096,
    "deepseek": 65536,
    "llama": 4096,
}

_PARAM_RE = re.compile(r"([\d.]+)\s*([BKMGT]?)", re.IGNORECASE)


def _parse_param_count(raw: str) -> str:
    """Normalise Ollama's '3.2B', '7b', '70B' etc. to the catalog 'XB' format."""
    if not raw:
        return ""
    m = _PARAM_RE.match(raw.strip())
    if not m:
        return ""
    val, suffix = m.group(1), m.group(2).upper()
    return f"{val}{suffix or 'B'}"


def _infer_context(details: dict) -> int:
    """Return a plausible default context from the Ollama family metadata."""
    families = list(details.get("families") or [])
    if details.get("family"):
        families.append(details["family"])
    for fam in families:
        fam = fam.lower()
        for key, ctx in _CONTEXT_BY_FAMILY.items():
            if key in fam:
                return ctx
    return 4096


def fetch_ollama_models(host: str = "") -> list:
    """Query a local Ollama instance and return hwfit-compatible model dicts.

    Returns an empty list silently when Ollama is not running or when a remote
    host is requested (SSH tunnelling is not yet implemented here).

    Results are cached for _CACHE_TTL seconds to avoid adding latency on
    every What Fits? scan request when Ollama is absent.
    """
    global _ollama_cache, _ollama_cache_ts

    if host:
        # Remote Ollama is accessible via the serve command but the hwfit
        # catalog fetch cannot SSH-tunnel a urllib call yet — return nothing.
        return []

    now = time.monotonic()
    if _ollama_cache is not None and (now - _ollama_cache_ts) < _CACHE_TTL:
        return list(_ollama_cache)

    urls = [
        "http://127.0.0.1:11434/api/tags",
        "http://host.docker.internal:11434/api/tags",
    ]
    raw = None
    for url in urls:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                raw = json.loads(r.read().decode("utf-8", "replace"))
            break
        except Exception:
            continue

    if not raw:
        # Cache the empty result so rapid successive calls don't all wait 1s×2.
        _ollama_cache = []
        _ollama_cache_ts = now
        return []

    models = []
    seen: set[str] = set()
    for item in raw.get("models", []):
        name = (item.get("name") or item.get("model") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)

        details = item.get("details") or {}
        param_str = details.get("parameter_size") or ""
        quant = (details.get("quantization_level") or "Q4_K_M").upper()
        ctx = _infer_context(details)

        models.append({
            "name": name,
            "provider": "Ollama",
            "parameter_count": _parse_param_count(param_str),
            "quantization": quant,
            "quant": quant,
            # Ollama stores all models as GGUF internally. Setting both fields
            # ensures the fit ranker uses single-GPU VRAM (not multi-GPU sharding)
            # and that Windows/Metal/RDNA filters don't exclude the entry.
            "is_gguf": True,
            "gguf_sources": [{"repo": name, "kind": "GGUF"}],
            "is_ollama": True,
            "backend": "ollama",
            "context_length": ctx,
            "context": ctx,
            "_source": "ollama",
        })

    _ollama_cache = models
    _ollama_cache_ts = now
    return list(models)


def invalidate_cache() -> None:
    """Force the next fetch_ollama_models() call to re-query Ollama.
    Useful after a model is downloaded or removed via Odysseus."""
    global _ollama_cache_ts
    _ollama_cache_ts = 0.0
