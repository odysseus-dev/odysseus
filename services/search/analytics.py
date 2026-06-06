"""Search analytics, metrics tracking, and exception hierarchy."""

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Dict, Any

from .cache import cache_metrics

logger = logging.getLogger(__name__)

# Dedicated error logger with file handler
_error_log_path = Path(__file__).resolve().parent.parent / "search_engine_error.log"
_error_handler = logging.FileHandler(_error_log_path, encoding="utf-8")
_error_handler.setLevel(logging.WARNING)
_error_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
error_logger = logging.getLogger("search_engine_error")
error_logger.addHandler(_error_handler)
error_logger.propagate = False

# Analytics file
ANALYTICS_FILE = Path(__file__).resolve().parent.parent / "search_analytics.json"


# ----------------------------------------------------------------------
# Custom exception hierarchy
# ----------------------------------------------------------------------
class SearchEngineError(Exception):
    """Base class for all search-engine related errors."""


class NetworkError(SearchEngineError):
    """Raised when a network request fails (e.g., timeout, DNS error)."""


class ParseError(SearchEngineError):
    """Raised when HTML or other content cannot be parsed."""


class RateLimitError(SearchEngineError):
    """Raised when the remote service returns a rate-limit (HTTP 429)."""


# ----------------------------------------------------------------------
# Analytics helpers
# ----------------------------------------------------------------------
def _default_analytics() -> Dict[str, Any]:
    return {
        "total_queries": 0,
        "successful_queries": 0,
        "failed_queries": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "query_patterns": {},
    }


def _load_analytics() -> Dict[str, Any]:
    """Load analytics data from the JSON file, creating defaults if missing."""
    if not ANALYTICS_FILE.exists():
        default = _default_analytics()
        _save_analytics(default)
        return default
    try:
        with open(ANALYTICS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Merge over defaults so a file written by an older schema (or a
        # partial write) still has every counter — _record_query indexes
        # these keys directly and would otherwise raise KeyError.
        merged = _default_analytics()
        if isinstance(data, dict):
            merged.update(data)
        return merged
    except Exception as e:
        logger.warning(f"Failed to load analytics file: {e}")
        return _default_analytics()


def _save_analytics(data: Dict[str, Any]) -> None:
    """Persist analytics data to the JSON file."""
    try:
        with open(ANALYTICS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to write analytics file: {e}")


def _record_query(query: str, success: bool, cache_hit: bool) -> None:
    """Update analytics for a single query execution."""
    analytics = _load_analytics()
    analytics["total_queries"] += 1
    if success:
        analytics["successful_queries"] += 1
    else:
        analytics["failed_queries"] += 1

    if cache_hit:
        analytics["cache_hits"] += 1
        cache_metrics["hits"] += 1
    else:
        analytics["cache_misses"] += 1
        cache_metrics["misses"] += 1

    patterns = analytics["query_patterns"]
    entry = patterns.get(query, {"count": 0, "successes": 0})
    entry["count"] += 1
    if success:
        entry["successes"] += 1
    patterns[query] = entry

    _save_analytics(analytics)


def get_search_stats() -> Dict[str, Any]:
    """Return aggregated search analytics."""
    analytics = _load_analytics()
    total = analytics.get("total_queries", 0) or 1
    success_rate = analytics.get("successful_queries", 0) / total
    cache_total = analytics.get("cache_hits", 0) + analytics.get("cache_misses", 0) or 1
    cache_hit_rate = analytics.get("cache_hits", 0) / cache_total

    pattern_counter = Counter({
        q: data["count"] for q, data in analytics.get("query_patterns", {}).items()
    })
    most_common = [q for q, _ in pattern_counter.most_common(5)]

    return {
        "most_common_queries": most_common,
        "success_rate": success_rate,
        "cache_hit_rate": cache_hit_rate,
        "total_queries": analytics.get("total_queries", 0),
        "successful_queries": analytics.get("successful_queries", 0),
        "failed_queries": analytics.get("failed_queries", 0),
        "cache_hits": analytics.get("cache_hits", 0),
        "cache_misses": analytics.get("cache_misses", 0),
        "cache_evictions": cache_metrics["evictions"],
        "runtime_cache_hits": cache_metrics["hits"],
        "runtime_cache_misses": cache_metrics["misses"],
    }
