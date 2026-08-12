"""Search and content caching with LRU eviction."""

import hashlib
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict

from core.constants import DATA_DIR

logger = logging.getLogger(__name__)

# Cache directories
CACHE_DIR = Path(DATA_DIR) / "cache"
SEARCH_CACHE_DIR = CACHE_DIR / "search"
CONTENT_CACHE_DIR = CACHE_DIR / "content"
CACHE_MAX_ENTRIES = 1000

# Create cache directories. Guarded so an unwritable path (e.g. a read-only
# mount) degrades to no-disk-cache instead of crashing module import.
try:
    SEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CONTENT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
except OSError as _e:
    logger.warning("Search cache directory unavailable (%s); disk cache disabled", _e)

# Track cache size for LRU eviction
search_cache_index: Dict[str, datetime] = {}
content_cache_index: Dict[str, datetime] = {}

# Cache metrics (shared across modules)
cache_metrics = {"hits": 0, "misses": 0, "evictions": 0}


def generate_cache_key(data: str) -> str:
    """Generate a unique cache key using SHA-256 hash."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def cleanup_cache(cache_dir: Path, cache_index: Dict[str, datetime], max_age: timedelta):
    """Remove expired cache entries and enforce LRU policy."""
    current_time = datetime.now()
    files_in_dir = {f.stem: f for f in cache_dir.glob("*.cache")}

    live_entries = []
    for key, cache_file in list(files_in_dir.items()):
        timestamp = cache_index.get(key)
        if timestamp is None:
            try:
                timestamp = datetime.fromtimestamp(cache_file.stat().st_mtime)
            except OSError:
                continue
        if current_time - timestamp > max_age:
            try:
                cache_file.unlink(missing_ok=True)
                cache_metrics["evictions"] += 1
                cache_index.pop(key, None)
            except OSError as e:
                logger.debug("Failed to remove expired cache file %s: %s", cache_file, e)
        else:
            live_entries.append((key, timestamp, cache_file))

    for key in list(cache_index):
        if key not in files_in_dir:
            cache_index.pop(key, None)
            cache_metrics["evictions"] += 1

    if len(live_entries) > CACHE_MAX_ENTRIES:
        excess_count = len(live_entries) - CACHE_MAX_ENTRIES
        for key, _, cache_file in sorted(live_entries, key=lambda x: x[1])[:excess_count]:
            try:
                cache_file.unlink(missing_ok=True)
                cache_metrics["evictions"] += 1
                cache_index.pop(key, None)
            except OSError as e:
                logger.debug("Failed to remove excess cache file %s: %s", cache_file, e)
