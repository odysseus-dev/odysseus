"""Shared L2 LLM response cache backed by Valkey (betterdb-agent-cache).

This sits *behind* the existing in-process cache in ``src/llm_core.py``: that
process-local dict stays the L1 (fast, always on), and this module adds an
optional L2 that is shared across workers/replicas and survives a restart.

Design notes
------------
* **Opt-in and fail-open.** Everything is gated on ``VALKEY_URL``. When it is
  unset, the package is missing, or the server is unreachable, every call here
  becomes a no-op (returns ``None`` / does nothing) and the LLM path proceeds
  exactly as before. A cache must never be able to break a chat turn.

* **One cache, both call paths.** ``betterdb-agent-cache`` is async-native
  (``valkey.asyncio``). ``llm_core`` calls the cache from both a synchronous
  path (``llm_call``, which runs in FastAPI's threadpool) and an async path
  (``llm_call_async``, on the main event loop). To serve both from a single
  ``AgentCache`` without binding an asyncio client to two loops, we run the
  cache on its own dedicated background event-loop thread and submit coroutines
  to it with ``run_coroutine_threadsafe`` - the sync path blocks on the future,
  the async path awaits it without blocking the main loop.

* **Route partitioning preserved.** The agent-cache LLM key hashes
  ``model``/``messages``/``temperature``/``max_tokens`` but not the endpoint or
  credentials. Odysseus deliberately partitions its L1 key by url + credential
  identity so a reply cached for one account/route is never served under
  another. We keep that property by folding the caller-supplied ``route`` string
  into the hashed messages as a synthetic leading system message (used only for
  the cache key - it is never sent to a model).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Dict, List, Optional

from src.constants import (
    VALKEY_CONNECT_TIMEOUT,
    VALKEY_LLM_TTL,
    VALKEY_OP_TIMEOUT,
    VALKEY_URL,
)

logger = logging.getLogger(__name__)

# Keep cache I/O off the critical path: if Valkey is slow we would rather miss
# than stall an LLM call. Connect is a one-time startup probe so it can be a
# little longer; per-op budgets are tight.
_CONNECT_TIMEOUT = VALKEY_CONNECT_TIMEOUT
_OP_TIMEOUT = VALKEY_OP_TIMEOUT
_TTL: Optional[int] = VALKEY_LLM_TTL if VALKEY_LLM_TTL > 0 else None


_lock = threading.Lock()
_loop: Optional[asyncio.AbstractEventLoop] = None
_cache: Any = None            # betterdb_agent_cache.AgentCache
_disabled = False             # latched True once we know there is no usable cache


def _start() -> Any:
    """Lazily bring up the background loop + AgentCache. Returns the cache or None.

    Latches ``_disabled`` on any failure so a missing/dead Valkey is probed once
    per process rather than on every LLM call.
    """
    global _loop, _cache, _disabled
    if _cache is not None or _disabled:
        return _cache
    with _lock:
        if _cache is not None or _disabled:
            return _cache

        url = VALKEY_URL
        if not url:
            _disabled = True
            return None

        try:
            import valkey.asyncio as valkey_async
            from betterdb_agent_cache import (
                AgentCache,
                AgentCacheOptions,
                TierDefaults,
            )
        except Exception as exc:  # package not installed - soft dependency
            logger.info("llm_cache: agent-cache/valkey not available (%s); L2 disabled", exc)
            _disabled = True
            return None

        loop = asyncio.new_event_loop()
        thread = threading.Thread(
            target=loop.run_forever, name="llm-cache-loop", daemon=True
        )
        thread.start()

        async def _init() -> Any:
            client = valkey_async.Valkey.from_url(url)
            await client.ping()  # fail fast if unreachable
            return AgentCache(
                AgentCacheOptions(
                    client=client,
                    name="odysseus_llm",
                    tier_defaults={"llm": TierDefaults(ttl=_TTL)},
                )
            )

        try:
            fut = asyncio.run_coroutine_threadsafe(_init(), loop)
            _cache = fut.result(timeout=_CONNECT_TIMEOUT)
        except Exception as exc:
            logger.warning("llm_cache: Valkey unavailable (%s); L2 disabled", exc)
            loop.call_soon_threadsafe(loop.stop)
            _disabled = True
            return None

        _loop = loop
        logger.info("llm_cache: L2 response cache enabled (name=odysseus_llm)")
        return _cache


def enabled() -> bool:
    """True once an L2 cache has been successfully brought up."""
    return _start() is not None


def _params(
    route: str,
    model: str,
    messages: List[Dict],
    temperature: float,
    max_tokens: int,
) -> Dict[str, Any]:
    # Fold the endpoint/credential identity into the hashed messages so replies
    # stay partitioned by route (the agent-cache key does not include it). This
    # synthetic message is only ever hashed, never sent to a model.
    cache_messages = [
        {"role": "system", "content": "[odysseus-cache-route] " + route}
    ] + list(messages)
    params: Dict[str, Any] = {
        "model": model,
        "messages": cache_messages,
        "temperature": temperature,
    }
    if isinstance(max_tokens, int) and max_tokens > 0:
        params["max_tokens"] = max_tokens
    return params


# ── async API (used by llm_call_async on the main event loop) ──

async def aget(
    route: str,
    model: str,
    messages: List[Dict],
    temperature: float,
    max_tokens: int,
) -> Optional[str]:
    cache = _start()
    if cache is None or _loop is None:
        return None
    try:
        params = _params(route, model, messages, temperature, max_tokens)
        fut = asyncio.run_coroutine_threadsafe(cache.llm.check(params), _loop)
        result = await asyncio.wait_for(asyncio.wrap_future(fut), timeout=_OP_TIMEOUT)
        return result.response if result.hit else None
    except Exception as exc:
        logger.debug("llm_cache.aget failed (%s); treating as miss", exc)
        return None


async def aset(
    route: str,
    model: str,
    messages: List[Dict],
    temperature: float,
    max_tokens: int,
    response: str,
) -> None:
    cache = _start()
    if cache is None or _loop is None or not response:
        return
    try:
        params = _params(route, model, messages, temperature, max_tokens)
        fut = asyncio.run_coroutine_threadsafe(
            cache.llm.store(params, response), _loop
        )
        await asyncio.wait_for(asyncio.wrap_future(fut), timeout=_OP_TIMEOUT)
    except Exception as exc:
        logger.debug("llm_cache.aset failed (%s); skipping store", exc)


# ── sync API (used by llm_call inside the FastAPI threadpool) ──

def get(
    route: str,
    model: str,
    messages: List[Dict],
    temperature: float,
    max_tokens: int,
) -> Optional[str]:
    cache = _start()
    if cache is None or _loop is None:
        return None
    try:
        params = _params(route, model, messages, temperature, max_tokens)
        fut = asyncio.run_coroutine_threadsafe(cache.llm.check(params), _loop)
        result = fut.result(timeout=_OP_TIMEOUT)
        return result.response if result.hit else None
    except Exception as exc:
        logger.debug("llm_cache.get failed (%s); treating as miss", exc)
        return None


def set(
    route: str,
    model: str,
    messages: List[Dict],
    temperature: float,
    max_tokens: int,
    response: str,
) -> None:
    cache = _start()
    if cache is None or _loop is None or not response:
        return
    try:
        params = _params(route, model, messages, temperature, max_tokens)
        fut = asyncio.run_coroutine_threadsafe(
            cache.llm.store(params, response), _loop
        )
        fut.result(timeout=_OP_TIMEOUT)
    except Exception as exc:
        logger.debug("llm_cache.set failed (%s); skipping store", exc)
