"""
Shared event/state types for browser recipes.

Keep this small and stable; new adapters add their own event kinds
but should reuse the base shape wherever possible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LifecycleEvent:
    kind: str
    payload: dict
    ts_ms: int
    adapter: str


@dataclass
class RecipeState:
    adapter: str
    started_at: Optional[int] = None
    last_event_at: Optional[int] = None
    last_event_key: str = ""
    metadata: dict = field(default_factory=dict)
