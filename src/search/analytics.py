"""Compatibility re-export shim for the live analytics module.

The real implementation lives in :mod:`services.search.analytics`, which is what
the search runtime (services/search/core.py) and docker/entrypoint.sh use. This
module used to hold a parallel copy; it now re-exports so the two cannot drift
out of sync again.
"""

from services.search.analytics import (  # noqa: F401
    ANALYTICS_FILE,
    error_logger,
    SearchEngineError,
    NetworkError,
    ParseError,
    RateLimitError,
    _default_analytics,
    _load_analytics,
    _save_analytics,
    _record_query,
    get_search_stats,
)
