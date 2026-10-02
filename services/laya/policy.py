# services/laya/policy.py
"""Caller-side policy: redaction + confidence thresholds.

laya emits calibrated probabilities, but *whether to act* on them is Odysseus's
decision, not the model's. Thresholds live here and are env-tunable. Redaction
also lives here so the audit layer never has to decide what's safe to persist.
"""

from __future__ import annotations

import hashlib
import os
from typing import Tuple

_PREVIEW_CHARS = 120


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def route_min_confidence() -> float:
    """Below this, a routing suggestion is advisory-only (not acted on)."""
    return _env_float("LAYA_ROUTE_MIN_CONFIDENCE", 0.70)


def guard_threshold() -> float:
    """At/above this P(attack), the guard capability would flag the input."""
    return _env_float("LAYA_GUARD_THRESHOLD", 0.80)


def moderation_threshold() -> float:
    """At/above this normalized harm score [0,1], content would be flagged."""
    return _env_float("LAYA_MODERATION_THRESHOLD", 0.60)


def redact(text: str) -> Tuple[str, str, int]:
    """Return (preview, sha256_hex, length) for an input string.

    The raw text is never persisted — only a short, whitespace-collapsed preview
    (for human spot-checks), a SHA-256 (for correlation), and the length.
    """
    s = text or ""
    raw = s.encode("utf-8", "replace")
    digest = hashlib.sha256(raw).hexdigest()
    collapsed = " ".join(s.split())
    preview = collapsed[:_PREVIEW_CHARS]
    if len(collapsed) > _PREVIEW_CHARS:
        preview += "…"
    return preview, digest, len(s)
