"""Search service — web search with SearXNG."""

from .analytics import (
    NetworkError,
    ParseError,
    RateLimitError,
    SearchEngineError,
    get_search_stats,
)
from .content import fetch_webpage_content
from .core import (
    comprehensive_web_search,
    get_search_config,
    invalidate_search_cache,
    searxng_search_results,
    update_search_config,
)
from .providers import PROVIDER_INFO, searxng_search, searxng_search_api
from .service import SearchResponse, SearchResult, SearchService

__all__ = [
    # Service interface (preferred)
    "SearchService",
    "SearchResult",
    "SearchResponse",
    # Low-level functions (for backwards compat)
    "comprehensive_web_search",
    "fetch_webpage_content",
    "get_search_config",
    "get_search_stats",
    "invalidate_search_cache",
    "searxng_search",
    "searxng_search_api",
    "searxng_search_results",
    "update_search_config",
    "PROVIDER_INFO",
    "SearchEngineError",
    "NetworkError",
    "ParseError",
    "RateLimitError",
]
