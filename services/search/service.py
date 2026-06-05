# services/search/service.py
"""Search service — clean interface for web search."""

from dataclasses import dataclass
from typing import List, Optional, Dict, Any

from . import (
    comprehensive_web_search,
    fetch_webpage_content,
    get_search_config,
)


@dataclass
class SearchResult:
    """A single search result."""
    url: str
    title: str
    snippet: str
    content: Optional[str] = None


@dataclass
class SearchResponse:
    """Response from a search query."""
    query: str
    results: List[SearchResult]
    total: int
    cached: bool = False


class SearchService:
    """
    Web search service.

    Usage:
        service = SearchService()
        result = await service.search("python async patterns")
        for r in result.results:
            print(f"{r.title}: {r.url}")
    """

    def __init__(self, default_depth: int = 1, fetch_content: bool = True):
        self.default_depth = default_depth
        self.fetch_content = fetch_content

    async def search(
        self,
        query: str,
        depth: Optional[int] = None,
        fetch_content: Optional[bool] = None,
    ) -> SearchResponse:
        """
        Search the web.

        Args:
            query: Search query
            depth: Search depth (1=quick, 2=thorough, 3=comprehensive)
            fetch_content: Whether to fetch full page content

        Returns:
            SearchResponse with results
        """
        depth = depth or self.default_depth

        # comprehensive_web_search is synchronous and, with return_sources=True,
        # returns (context_str, [{"url", "title"}, ...]). Run it off the event
        # loop so we don't block it, and use the source list as the result rows.
        # `fetch_content` is accepted for API compatibility; the comprehensive
        # search always fetches page content.
        import asyncio
        _context, raw_results = await asyncio.to_thread(
            comprehensive_web_search,
            query,
            max_pages=10 * depth,
            return_sources=True,
        )

        results = []
        for r in raw_results:
            if not isinstance(r, dict):
                continue
            results.append(SearchResult(
                url=r.get("url", ""),
                title=r.get("title", ""),
                snippet=r.get("snippet", ""),
                content=r.get("content"),
            ))

        return SearchResponse(
            query=query,
            results=results,
            total=len(results),
        )

    async def fetch_content(self, url: str) -> Optional[str]:
        """Fetch content from a URL."""
        return await fetch_webpage_content(url)

    def get_config(self) -> Dict[str, Any]:
        """Get current search configuration."""
        return get_search_config()
