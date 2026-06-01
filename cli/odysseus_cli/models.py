"""Query the OpenAI-compatible endpoint for its installed models.

Ollama (and vLLM / llama.cpp) expose ``GET {base}/v1/models`` returning the
OpenAI list shape: ``{"object": "list", "data": [{"id": "...", ...}, ...]}``.
We use stdlib urllib so the CLI needs no extra dependency for this.
"""

from __future__ import annotations

import json
import urllib.request
from typing import List, Optional


def models_url(base: str) -> str:
    """Derive the /v1/models URL from any accepted endpoint form."""
    u = base.rstrip("/")
    if u.endswith("/chat/completions"):
        u = u[: -len("/chat/completions")]
    u = u.rstrip("/")
    if not u.endswith("/v1"):
        u = u + "/v1"
    return u + "/models"


def list_models(endpoint: str, api_key: Optional[str] = None,
                timeout: float = 5.0) -> List[str]:
    """Return the list of model ids served at the endpoint.

    Returns an empty list on any failure (network, parse, non-200) — callers
    treat an empty list as "couldn't reach the server / none installed".
    """
    url = models_url(endpoint)
    req = urllib.request.Request(url)
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    ids = [m.get("id") for m in data if isinstance(m, dict) and m.get("id")]
    return sorted(ids)
