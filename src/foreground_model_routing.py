"""Foreground Chat and Agent model-routing policy.

The selected session model is strict by default. Historical
``default_model_fallbacks`` values remain stored for compatibility, but this
policy intentionally does not read or migrate them.
"""

from typing import Any, Dict, Optional


def resolve_foreground_fallback_candidates(owner: Optional[str] = None) -> list:
    """Return fallback candidates for a foreground Chat or Agent request.

    Foreground routing is strict, so no alternate endpoint/model is eligible.
    ``owner`` is accepted to keep this policy boundary owner-aware.
    """

    del owner
    return []


def build_foreground_model_candidates(
    endpoint_url: str,
    model: str,
    headers: Optional[Dict[str, Any]] = None,
    owner: Optional[str] = None,
) -> list:
    """Build the ordered candidate list for a foreground request."""

    primary = (endpoint_url, model, headers or {})
    return [primary] + resolve_foreground_fallback_candidates(owner=owner)
