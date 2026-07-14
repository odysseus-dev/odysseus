"""Pixel retrieval — historical visual recall via the unified memory API.

Backs the `screen_recall` agent tool. Queries :40001/query (PixelRAG visual
tiles + agent memory + MemPalace notes in one call). CPU query embedding can
take minutes, so the timeout is generous and a timeout is a structured
degraded result, never a raw socket exception.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from typing import Any, Dict
from urllib import error, request

from services.operator.core import (
    CAP_PIXEL_RETRIEVAL,
    HINTS,
    degraded_envelope,
    envelope,
    require_capability,
    unified_memory_url,
)

logger = logging.getLogger(__name__)

DEFAULT_K = 5
MAX_K = 20


def _recall_timeout() -> float:
    try:
        return float(os.environ.get("OPERATOR_RECALL_TIMEOUT") or 120.0)
    except ValueError:
        return 120.0


def screen_recall(query: str, k: int = DEFAULT_K) -> Dict[str, Any]:
    """Semantic retrieval over indexed screen history + memories + notes."""
    query = (query or "").strip()
    if not query:
        return envelope(CAP_PIXEL_RETRIEVAL, False, reason="empty_query")

    gate = require_capability(CAP_PIXEL_RETRIEVAL)
    if gate:
        return gate

    k = max(1, min(int(k or DEFAULT_K), MAX_K))
    url = f"{unified_memory_url()}/query"
    payload = json.dumps({"query": query, "k": k}).encode("utf-8")
    req = request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=_recall_timeout()) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (socket.timeout, TimeoutError):
        return degraded_envelope(
            CAP_PIXEL_RETRIEVAL, "timeout",
            hint="The index is up but query embedding is slow — retry with a shorter query.",
        )
    except error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), (socket.timeout, TimeoutError)):
            return degraded_envelope(
                CAP_PIXEL_RETRIEVAL, "timeout",
                hint="The index is up but query embedding is slow — retry with a shorter query.",
            )
        return degraded_envelope(CAP_PIXEL_RETRIEVAL, f"unified_memory_error: {exc}")
    except (json.JSONDecodeError, OSError, ValueError):
        return degraded_envelope(CAP_PIXEL_RETRIEVAL, "unified_memory_bad_response")

    if not isinstance(body, dict):
        return degraded_envelope(CAP_PIXEL_RETRIEVAL, "unified_memory_bad_response")

    err = str(body.get("error") or "")
    if err and ("index" in err.lower() or "faiss" in err.lower()):
        return degraded_envelope(
            CAP_PIXEL_RETRIEVAL, "no_index", hint=HINTS[CAP_PIXEL_RETRIEVAL],
        )

    data: Dict[str, Any] = {
        "query": query,
        "visual_results": body.get("visual_results") or [],
        "agent_memory_results": body.get("agent_memory_results") or [],
        "notes_results": body.get("notes_results") or [],
    }
    if err:
        data["warning"] = err
    return envelope(CAP_PIXEL_RETRIEVAL, True, data=data)
