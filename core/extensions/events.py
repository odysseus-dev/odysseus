"""events.py — wire OXS extensions to the existing event_bus.

Two paths:
  - ``host.subscribe_event(name, handler)`` registers a Python callable that
    gets called every time ``fire_event(name)`` is invoked elsewhere.
    The bus itself is the registry's responsibility: ``wire_subscriptions``
    (called on enable) iterates the host's recorded subscriptions and
    registers them; ``unwire_subscriptions`` (called on disable) clears.

  - ``host.fire_event(name, payload, owner=None)`` is recorded by the host
    during the enable cycle and dispatched by the registry right after the
    hook chain completes — never synchronously inside the host. This
    avoids the extension racing against its own enable.

Both paths require the ``events_sub`` / ``events_fire`` capability. The
host enforces that; the registry only handles wiring.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ── Subscriber registry ────────────────────────────────────────────────────
#
# Subscriptions live in a module-level dict. Multiple extensions can
# subscribe to the same event name; every handler is invoked when the
# event fires. Handlers are plain callables, async via the loop the
# caller is in (event_bus.fire_event dispatches on the running loop).

_subscribers: dict[str, list[tuple[str, Callable]]] = {}
# Each entry: (extension_id, handler). We keep the extension_id for
# clean removal and for audit logging when handlers throw.


def wire_subscriptions(extension_id: str, subscriptions: list[tuple[str, Callable]]) -> int:
    """Register every (event_name, handler) pair the host collected.

    Returns the number of subscriptions registered.
    """
    count = 0
    for event_name, handler in subscriptions:
        if event_name not in _subscribers:
            _subscribers[event_name] = []
        _subscribers[event_name].append((extension_id, handler))
        count += 1
    return count


def unwire_subscriptions(extension_id: str) -> int:
    """Remove every subscription belonging to ``extension_id``.

    Returns the number of subscriptions removed.
    """
    removed = 0
    for event_name, handlers in list(_subscribers.items()):
        kept = [(xid, fn) for xid, fn in handlers if xid != extension_id]
        removed += len(handlers) - len(kept)
        if kept:
            _subscribers[event_name] = kept
        else:
            del _subscribers[event_name]
    return removed


def list_subscriptions() -> dict[str, list[str]]:
    """Return a map of event_name -> [extension_id, ...] for inspection."""
    return {k: sorted({xid for xid, _ in v}) for k, v in _subscribers.items()}


# ── Dispatcher ─────────────────────────────────────────────────────────────


def dispatch_event(event_name: str, payload: Any = None) -> int:
    """Invoke every subscriber for ``event_name``.

    Runs synchronously in the caller's context. Errors in handlers are
    caught, logged, and never propagated — one bad extension cannot stop
    another observer from seeing the event.

    Returns the number of handlers invoked.
    """
    handlers = _subscribers.get(event_name, [])
    invoked = 0
    for extension_id, handler in handlers:
        invoked += 1
        try:
            handler(payload)
        except Exception:
            logger.exception(
                "OXS event handler for %r raised (extension=%s)",
                event_name, extension_id,
            )
    return invoked


# ── Convenience: forward into the existing event_bus ───────────────────────


def dispatch_via_event_bus(event_name: str, owner: str | None = None, payload: Any = None) -> int:
    """Fire ``event_name`` through the existing ``src.event_bus.fire_event``
    and then dispatch to OXS subscribers.

    The existing bus already triggers scheduled-task actions; OXS
    subscribers ride alongside it. Operators keep one mental model:
    "events fire here, all listeners run".
    """
    from src.event_bus import fire_event
    fire_event(event_name, owner=owner)
    return dispatch_event(event_name, payload)


__all__ = [
    "wire_subscriptions",
    "unwire_subscriptions",
    "list_subscriptions",
    "dispatch_event",
    "dispatch_via_event_bus",
]