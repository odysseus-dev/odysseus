"""
Shared browser-recipe base helpers.
"""

from __future__ import annotations

from typing import Callable, Dict

from .schemas import LifecycleEvent, RecipeState


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)
