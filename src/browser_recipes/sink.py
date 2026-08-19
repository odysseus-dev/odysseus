"""
Event sink that forwards browser recipe events into odysseus.

Callers can add more sinks—memory store, notification channel,
webhook—by wrapping or replacing this function.
"""

from __future__ import annotations

import logging
from typing import Callable

from src.browser_recipes.schemas import LifecycleEvent

logger = logging.getLogger("browser_recipes.sink")


def default_sink(event: LifecycleEvent) -> None:
    logger.info(
        "recipe=%s kind=%s ts=%s payload_keys=%s",
        event.adapter,
        event.kind,
        event.ts_ms,
        list(event.payload.keys())[:8],
    )
