"""Research navigation primitives.

Deep Research historically had its own narrow Search -> Fetch path.  This
module gives the research engine a small normalized surface for richer web
navigation while still reusing Odysseus' existing web tooling.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
from dataclasses import dataclass
from typing import Any

from src.constants import MAX_OUTPUT_CHARS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResearchAction:
    """A bounded navigation action the research planner can request."""

    tool: str
    args: dict[str, Any]


@dataclass
class ResearchPage:
    """Normalized readable page content for research extraction."""

    url: str
    title: str = ""
    content: str = ""
    og_image: str = ""
    success: bool = False
    retrieval: str = "fetch"
    error: str = ""


@dataclass(frozen=True)
class ResearchSourceAssessment:
    """Simple quality metadata for a gathered research source."""

    kind: str
    score: int
    reason: str


def parse_research_actions(text: str, *, allowed_tools: set[str] | None = None) -> list[ResearchAction]:
    """Parse a model reply into bounded research actions.

    Accepts either a JSON array directly or an object with an ``actions`` array.
    This is intentionally small and strict so a later model-planned research
    loop can be added without giving the research model arbitrary tool access.
    """
    allowed = allowed_tools or {
        "web_search",
        "web_fetch",
        "browser_open",
        "browser_read",
        "browser_snapshot",
        "private_browser",
    }
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.removeprefix("```json").removeprefix("```").strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, dict):
        parsed = parsed.get("actions")
    if not isinstance(parsed, list):
        return []
    actions: list[ResearchAction] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        has_tool_field = "tool" in item
        tool = str(item.get("tool") or item.get("action") or "").strip()
        if tool not in allowed:
            continue
        args = item.get("args")
        if not isinstance(args, dict):
            excluded = {"tool"} if has_tool_field else {"action"}
            args = {k: v for k, v in item.items() if k not in excluded}
        actions.append(ResearchAction(tool=tool, args=args))
    return actions


def assess_source(url: str, *, title: str = "", retrieval: str = "", summary: str = "") -> ResearchSourceAssessment:
    """Estimate source usefulness for planning and reporting.

    This is deliberately coarse. The model still judges evidence content; this
    only gives the planner a compact map of whether it has official/primary
    sources or mostly secondary/search-result material.
    """
    parsed = urllib.parse.urlparse(str(url or ""))
    host = (parsed.netloc or parsed.path.split("/", 1)[0]).lower().removeprefix("www.")
    path = (parsed.path or "").lower()
    text = " ".join([host, path, str(title or ""), str(summary or "")]).lower()
    retrieval = (retrieval or "fetch").lower()

    kind = "secondary"
    score = 55
    reason = "secondary web source"

    if host.endswith((".gov", ".edu", ".ac.uk")) or ".gov." in host:
        kind, score, reason = "official", 90, "government/academic domain"
    elif any(part in host for part in ("github.com", "huggingface.co", "docs.", "developer.", "support.")):
        kind, score, reason = "primary", 82, "primary project/vendor source"
    elif any(token in text for token in ("official", "documentation", "docs", "release notes", "press release")):
        kind, score, reason = "primary", 78, "primary-source wording"
    elif any(part in host for part in ("reddit.com", "quora.com", "stackoverflow.com", "news.ycombinator.com")):
        kind, score, reason = "community", 45, "community/forum source"
    elif any(token in text for token in ("affiliate", "coupon", "best-", "top-", "review")):
        kind, score, reason = "commercial", 40, "commercial/listicle source"

    if retrieval == "browser":
        score = min(100, score + 5)
        reason += "; browser-read"
    if not str(summary or "").strip():
        score = max(10, score - 20)
        reason += "; weak extraction"

    return ResearchSourceAssessment(kind=kind, score=score, reason=reason)


class ResearchNavigator:
    """Small web-navigation facade used by Deep Research.

    It delegates to the same search/fetch/browser implementations the chat
    agent uses, but normalizes results for the research extraction pipeline.
    """

    def __init__(
        self,
        *,
        progress_callback=None,
        session_id: str = "",
        search_provider: str | None = None,
    ) -> None:
        self._progress = progress_callback
        self.session_id = session_id
        self.search_provider = (search_provider or "").strip()
        self.providers_used: list[str] = []
        self.last_search_error = ""
        self.browser_fetches = 0

    async def search(self, query: str, *, count: int = 10) -> list[dict[str, Any]]:
        """Run a provider-chain web search and return structured results."""
        try:
            from src.search.providers import _get_search_settings
            from src.search.core import _build_provider_chain, _call_provider

            settings = _get_search_settings()
            provider = self.search_provider or (settings.get("research_search_provider") or "").strip()
            if not provider:
                provider = settings.get("search_provider", "searxng")
            if provider == "disabled":
                logger.info("Search is disabled for research")
                return []

            chain = _build_provider_chain(provider)
            raised = False
            for prov in chain:
                try:
                    results = await asyncio.to_thread(_call_provider, prov, query, count)
                    if results:
                        if prov not in self.providers_used:
                            self.providers_used.append(prov)
                        return results
                except Exception as e:
                    raised = True
                    logger.warning("Research search provider %s failed: %s", prov, e)
                    self.last_search_error = f"{prov}: {e}"
            if not raised:
                self.last_search_error = (
                    "no results from search provider(s): "
                    f"{', '.join(chain) if chain else provider}"
                )
            return []
        except Exception as e:
            logger.error("Research search failed for %r: %s", query, e)
            self.last_search_error = str(e)
            return []

    async def fetch(self, url: str, *, timeout: int = 10, max_bytes: int | None = None) -> ResearchPage:
        """Fetch readable text from a URL using Odysseus' web fetcher."""
        try:
            from src.search.content import fetch_webpage_content

            kwargs: dict[str, Any] = {"timeout": timeout}
            if max_bytes is not None:
                kwargs["max_bytes"] = max_bytes
            page = await asyncio.to_thread(fetch_webpage_content, url, **kwargs)
        except Exception as e:
            return ResearchPage(url=url, success=False, error=str(e))
        return self._normalize_fetch_page(url, page)

    async def browser_read(self, url: str, *, timeout: int = 45) -> ResearchPage:
        """Read a JS-heavy page through the private browser tool."""
        from src.agent_tools.web_tools import PrivateBrowserTool

        if self._progress:
            self._progress({"phase": "navigating", "url": url, "title": url})
        tool = PrivateBrowserTool()
        result = await tool.execute(
            json.dumps({"action": "read", "url": url, "timeout_ms": int(timeout * 1000)}),
            {"session_id": self.session_id or "research"},
        )
        output = str(result.get("output") or "").strip()
        if result.get("exit_code") != 0 or not output:
            return ResearchPage(
                url=url,
                success=False,
                retrieval="browser",
                error=str(result.get("error") or output or "browser returned no readable content"),
            )
        self.browser_fetches += 1
        return ResearchPage(
            url=url,
            title=url,
            content=output[:MAX_OUTPUT_CHARS],
            success=True,
            retrieval="browser",
        )

    @staticmethod
    def _normalize_fetch_page(url: str, page: Any) -> ResearchPage:
        if not isinstance(page, dict):
            return ResearchPage(url=url, success=False, error="fetch returned non-object result")
        content = str(page.get("content") or "").strip()
        return ResearchPage(
            url=url,
            title=str(page.get("title") or ""),
            content=content,
            og_image=str(page.get("og_image") or ""),
            success=bool(page.get("success") and content),
            retrieval="fetch",
            error=str(page.get("error") or ""),
        )
