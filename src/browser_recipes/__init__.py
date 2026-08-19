"""
Optional integraton glue for browser recipes.

This module does NOT start anything on import. Call ``bootstrap_recipes()``
from a startup hook, admin route, or test fixture when the operator enables
browser recipes in settings / env.

Env:
  ODYSSEUS_BROWSER_USER_DATA_DIR  Chromium profile dir
  ODYSSEUS_BROWSER_HEADLESS       1/true/yes for headless
  ODYSSEUS_BROWSER_RECIPES       comma list: google_meet,linkedin
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Callable, Optional

from src.browser_recipes.browser_manager import PersistentBrowserManager
from src.browser_recipes.google_meet import MeetAdapter
from src.browser_recipes.linkedin import LinkedInAdapter
from src.browser_recipes.orchestrator import BrowserRecipeOrchestrator
from src.browser_recipes.schemas import LifecycleEvent
from src.browser_recipes.sink import default_sink

logger = logging.getLogger("browser_recipes.integration")


class BrowserRecipeRuntime:
    def __init__(self) -> None:
        self._manager: Optional[PersistentBrowserManager] = None
        self._orchestrator = BrowserRecipeOrchestrator()
        self._sink: Optional[Callable[[LifecycleEvent], None]] = None

    def configure_sink(self, sink: Callable[[LifecycleEvent], None]) -> None:
        self._sink = sink

    def _build_manager(self) -> PersistentBrowserManager:
        headless = os.environ.get("ODYSSEUS_BROWSER_HEADLESS", "1").lower() in ("1", "true", "yes")
        return PersistentBrowserManager(headless=headless)

    async def start(self) -> None:
        if self._manager is None:
            self._manager = self._build_manager()
        await self._manager.ensure_ready()
        if self._sink is None:
            self._orchestrator.bind_sink(default_sink)
        else:
            self._orchestrator.bind_sink(self._sink)
        enabled = [
            name.strip()
            for name in os.environ.get("ODYSSEUS_BROWSER_RECIPES", "google_meet,linkedin").split(",")
            if name.strip()
        ]
        if "google_meet" in enabled:
            self._orchestrator.register("google_meet", MeetAdapter(self._manager).run)
        if "linkedin" in enabled:
            self._orchestrator.register("linkedin", LinkedInAdapter(self._manager).run)
        await self._orchestrator.start()
        logger.info("browser recipes started: %s", enabled)

    async def stop(self) -> None:
        await self._orchestrator.stop()
        if self._manager:
            await self._manager.shutdown()
            self._manager = None
        logger.info("browser recipes stopped")


browser_recipe_runtime = BrowserRecipeRuntime()
