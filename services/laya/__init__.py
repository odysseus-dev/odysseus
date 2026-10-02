"""laya decision-engine integration (sidecar, HTTP).

Fast, local "System 1" typed decisions (model routing, prompt guardrails,
content moderation, request triage) served by a `laya-serve` sidecar container.
Off by default (`LAYA_ENABLED=false`); fail-open everywhere so a missing or
broken sidecar never degrades Odysseus. See docs/laya.md.
"""

from .config import LayaConfig
from .client import LayaClient, LayaError, LayaUnavailable, LayaInvalidResponse
from .decisions import LayaDecision
from .service import (
    LayaService,
    LayaHealth,
    GuardOutcome,
    get_laya_service,
    fire_shadow_route,
    is_paused,
    set_paused,
)

__all__ = [
    "LayaConfig",
    "LayaClient",
    "LayaError",
    "LayaUnavailable",
    "LayaInvalidResponse",
    "LayaDecision",
    "LayaService",
    "LayaHealth",
    "GuardOutcome",
    "get_laya_service",
    "fire_shadow_route",
    "is_paused",
    "set_paused",
]
