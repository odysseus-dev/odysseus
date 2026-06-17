"""Chat Gateway lifecycle — start/stop, wired into the FastAPI lifespan.

`start_chat_gateway()` is the single entrypoint app.py calls at startup. It
loads config, builds the enabled adapters, wires each to the shared
GatewayRunner, connects them, and launches their listen loops as asyncio tasks.
It returns the tasks so app.py can keep strong refs (see app.state._startup_tasks).
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from .config import load_gateway_config
from .registry import build_adapters
from .runner import GatewayRunner

logger = logging.getLogger("chat_gateway")

# Module-level handles so a future /admin endpoint or _shutdown_event can stop us.
_adapters = []
_tasks: List[asyncio.Task] = []
_runner: Optional[GatewayRunner] = None


async def _run_adapter(adapter) -> None:
    """Connect then listen forever, with a backoff reconnect guard."""
    try:
        ok = await adapter.connect()
    except Exception:
        logger.exception("[%s] connect raised", adapter.platform)
        ok = False
    if not ok:
        logger.warning("[%s] connect failed — adapter not started", adapter.platform)
        return
    backoff = 2
    while True:
        try:
            await adapter.listen()
            return  # clean shutdown
        except asyncio.CancelledError:
            await adapter.disconnect()
            raise
        except Exception:
            logger.exception("[%s] listen loop crashed; reconnecting in %ss", adapter.platform, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


def start_chat_gateway(session_manager) -> List[asyncio.Task]:
    """Build and launch the gateway. Returns the launched tasks (possibly empty).
    Safe to call unconditionally — does nothing if config is absent/disabled."""
    global _adapters, _tasks, _runner

    cfg = load_gateway_config()
    if not cfg.enabled:
        logger.info("chat_gateway: disabled (no config or enabled: false)")
        return []
    if not cfg.owner:
        logger.warning("chat_gateway: 'owner' not set in config — refusing to start (agent needs an identity)")
        return []

    _runner = GatewayRunner(cfg, session_manager)
    _adapters = build_adapters(cfg)
    if not _adapters:
        logger.warning("chat_gateway: no usable adapters — not starting")
        return []

    for adapter in _adapters:
        adapter.set_message_handler(_runner.handle_message)
        _tasks.append(asyncio.create_task(_run_adapter(adapter), name=f"chat_gateway:{adapter.platform}"))

    logger.info("chat_gateway: started %d adapter(s): %s",
                len(_tasks), ", ".join(a.platform for a in _adapters))
    return _tasks


async def stop_chat_gateway() -> None:
    for t in _tasks:
        t.cancel()
    for a in _adapters:
        try:
            await a.disconnect()
        except Exception:
            pass
    _tasks.clear()
    _adapters.clear()
