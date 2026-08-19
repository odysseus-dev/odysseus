"""
Manages a singleton Playwright Chromium context for background recipes.

Unlike standalone Playwright scripts, multiple recipes share one browser
instance and each gets its own isolated context. That keeps logins, cookies,
and Meet captions preferences in the assigned user profile while preventing
recipe-to-recipe state leaks.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from playwright.async_api import BrowserContext, Playwright, async_playwright

logger = logging.getLogger("browser_recipes")


class PersistentBrowserManager:
    def __init__(
        self,
        user_data_dir: Optional[str] = None,
        headless: bool = True,
        executable_path: Optional[str] = None,
    ) -> None:
        self._user_data_dir = user_data_dir or os.environ.get(
            "ODYSSEUS_BROWSER_USER_DATA_DIR",
            str(Path.home() / "AppData" / "Local" / "odysseus" / "playwright-profile"),
        )
        self._headless = headless
        self._executable_path = executable_path
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._lock = asyncio.Lock()

    async def ensure_ready(self) -> BrowserContext:
        async with self._lock:
            if self._context and not self._context.is_closed():
                return self._context
            return await self._launch()

    async def _launch(self) -> BrowserContext:
        logger.info(
            "launching persistent browser context from user_data_dir=%s headless=%s",
            self._user_data_dir,
            self._headless,
        )
        if self._playwright is None:
            self._playwright = await async_playwright().start()
        try_close = False
        if self._context and not self._context.is_closed():
            try:
                new_ctx = await self._playwright.chromium.launch_persistent_context(
                    user_data_dir=self._user_data_dir,
                    headless=self._headless,
                    executable_path=self._executable_path,
                    args=["--disable-blink-features=AutomationControlled"],
                    viewport={"width": 1280, "height": 800},
                    no_viewport=False,
                )
                self._context = new_ctx
                return new_ctx
            except Exception:
                try_close = True
        if try_close or self._context is None or self._context.is_closed():
            try:
                if self._context and not self._context.is_closed():
                    await self._context.close()
            except Exception:
                pass
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=self._user_data_dir,
                headless=self._headless,
                executable_path=self._executable_path,
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 800},
                no_viewport=False,
            )
            return self._context

    async def snapshot(self) -> dict:
        ctx = await self.ensure_ready()
        page = await ctx.new_page()
        try:
            return {
                "url": page.url,
                "title": await page.title(),
            }
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def new_page(self):
        ctx = await self.ensure_ready()
        return await ctx.new_page()

    async def shutdown(self) -> None:
        async with self._lock:
            try:
                if self._context and not self._context.is_closed():
                    await self._context.close()
            except Exception:
                pass
            self._context = None
            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
