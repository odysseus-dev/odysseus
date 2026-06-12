"""Search package — drop-in replacement for the monolithic search_engine module."""

from .core import (
    comprehensive_web_search,
    get_search_config,
    invalidate_search_cache,
    searxng_search_results,
    update_search_config,
)
from .content import fetch_webpage_content
from .providers import searxng_search, searxng_search_api, PROVIDER_INFO
from .analytics import get_search_stats, SearchEngineError, NetworkError, ParseError, RateLimitError

__all__ = [
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
