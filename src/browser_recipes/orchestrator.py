"""
Background orchestrator for browser recipe adapters.

Runs selected adapters concurrently, dedupes by adapter + key,
and forwards normalized events into ``src.event_bus``.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Awaitable, Callable, Dict, List, Optional

from .schemas import LifecycleEvent, RecipeState

logger = logging.getLogger(__name__)

EventSink = Callable[[LifecycleEvent], None]
TaskFactory = Callable[[EventSink, RecipeState], Awaitable[None]]


class BrowserRecipeOrchestrator:
    def __init__(self, sink: Optional[EventSink] = None) -> None:
        self._sink = sink
        self._states: Dict[str, RecipeState] = {}
        self._factories: Dict[str, TaskFactory] = {}
        self._tasks: Dict[str, asyncio.Task[None]] = {}
        self._running = False
        self._stop_event = asyncio.Event()

    def bind_sink(self, sink: EventSink) -> None:
        self._sink = sink

    def register(self, name: str, factory: TaskFactory) -> None:
        self._factories[name] = factory
        self._states[name] = RecipeState(adapter=name)

    async def start(self) -> None:
        if self._running:
            return
        if self._sink is None:
            raise RuntimeError("bind_sink() must be called before start()")
        self._running = True
        self._stop_event.clear()
        for name, factory in list(self._factories.items()):
            self._tasks[name] = asyncio.create_task(self._wrap(name, factory))

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()

    async def _wrap(self, name: str, factory: TaskFactory) -> None:
        backoff = 1.0
        state = self._states[name]
        while self._running:
            try:
                await factory(self._sink, state)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "browser recipe %s crashed: %s", name, exc, exc_info=True
                )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=backoff + random.uniform(0, backoff)
                )
            except asyncio.TimeoutError:
                backoff = min(backoff * 2, 60.0)
            except asyncio.CancelledError:
                raise
