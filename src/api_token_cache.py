"""Process-local API-token cache invalidation bridge.

HTTP routes can reach ``request.app.state`` directly, but agent tools cannot.
The application registers the same invalidator here so every token mutation
marks bearer-auth state dirty immediately.
"""

from __future__ import annotations

from collections.abc import Callable
from threading import Lock

_lock = Lock()
_invalidator: Callable[[], None] | None = None


def register_token_cache_invalidator(invalidator: Callable[[], None] | None) -> None:
    global _invalidator
    with _lock:
        _invalidator = invalidator if callable(invalidator) else None


def invalidate_token_cache() -> bool:
    with _lock:
        invalidator = _invalidator
    if not invalidator:
        return False
    try:
        invalidator()
        return True
    except Exception:
        return False
