"""Regression test for invalidate_search_cache key construction.

The write path (`searxng_search_results`) stores a cache entry under
``generate_cache_key(f"{query}|{count}|{time_filter}")`` where ``count`` is the
admin-configured result count (``_get_result_count()``, default **5**) — it
replaces the caller's default of 10 with the configured value before building
the key.

The original ``invalidate_search_cache`` hardcoded ``f"{query}|10|None"``, so it
never matched the key the write path actually produced (``|5|None`` by default)
and silently failed to invalidate anything — a contract violation of its own
docstring ("invalidate ... just the given query"). The fix derives the count
from ``_get_result_count()`` so invalidation matches the stored default entry.
"""
import pytest

from src.search import core
from src.search.cache import generate_cache_key


def test_invalidate_uses_configured_count_not_hardcoded_10(tmp_path, monkeypatch):
    query = "python tutorial"
    result_count = 5  # documented default of _get_result_count()

    # Pin the configured count and redirect the cache dir to keep the test hermetic.
    monkeypatch.setattr(core, "_get_result_count", lambda: result_count)
    monkeypatch.setattr(core, "SEARCH_CACHE_DIR", tmp_path)

    # Reproduce exactly what searxng_search_results writes for a default search:
    # the caller's default count of 10 is replaced by result_count, time_filter=None.
    write_key = generate_cache_key(f"{query}|{result_count}|None")
    cache_file = tmp_path / f"{write_key}.cache"
    cache_file.write_text("{}", encoding="utf-8")
    core.search_cache_index[write_key] = None

    try:
        core.invalidate_search_cache(query)

        assert not cache_file.exists(), (
            "invalidate_search_cache failed to remove the entry the write path "
            "stored under the configured result count — it used a mismatched key."
        )
        assert write_key not in core.search_cache_index
    finally:
        core.search_cache_index.pop(write_key, None)
