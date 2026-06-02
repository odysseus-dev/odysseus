"""Resolve auth headers when a session switches model/endpoint mid-chat (#1186).

The bug: PATCH /session/{sid} only refreshed the session's auth headers when an
``endpoint_id`` was supplied. If it was omitted, the *previous* endpoint's API key
stayed attached to the session and was sent to the *new* endpoint's URL, so the
provider rejected it with 401 (e.g. switching Groq → Cerebras).

The rule here: a switch ALWAYS rebuilds headers for the new endpoint, never the
old one. The key is resolved from the matching stored endpoint (by id, else by
URL); if none is found, headers are rebuilt with no key, which *replaces* (drops)
any stale Authorization rather than leaking the previous endpoint's credential.
"""

from typing import Callable, Dict, List, Optional


def _norm_url(u: Optional[str]) -> str:
    """Normalise an endpoint URL for comparison: drop a trailing
    ``/chat/completions`` and any trailing slashes."""
    s = (u or "").strip().rstrip("/")
    suffix = "/chat/completions"
    if s.endswith(suffix):
        s = s[: -len(suffix)].rstrip("/")
    return s


def find_session_endpoint(
    endpoints: List, endpoint_id: Optional[str], endpoint_url: Optional[str]
):
    """Find the stored endpoint for a switch: by id first, else by URL.

    ``endpoints`` is any iterable of objects with ``.id``, ``.base_url`` and
    ``.api_key``. Returns the matching endpoint or ``None``.
    """
    if endpoint_id:
        for ep in endpoints:
            if getattr(ep, "id", None) == endpoint_id:
                return ep
        return None
    if endpoint_url:
        target = _norm_url(endpoint_url)
        # EXACT normalized base match only. Prefix matching is unsafe for picking
        # an API key — two endpoints sharing a prefix (or one being a shorter
        # provider base) could attach the wrong saved credential. No exact match
        # → None → headers are cleared rather than a key guessed.
        for ep in endpoints:
            if target and _norm_url(getattr(ep, "base_url", None)) == target:
                return ep
    return None


def build_switch_headers(
    ep, endpoint_url: Optional[str], build_headers: Callable[[Optional[str], str], Dict[str, str]]
) -> Dict[str, str]:
    """Build the new session headers after a model/endpoint switch.

    Always derives from the new endpoint. With no matched endpoint, ``key`` is
    empty so ``build_headers`` emits no Authorization — clearing the old key.
    """
    key = (getattr(ep, "api_key", None) or "") if ep is not None else ""
    base = (getattr(ep, "base_url", None) or endpoint_url or "") if ep is not None else (endpoint_url or "")
    return build_headers(key, base)
