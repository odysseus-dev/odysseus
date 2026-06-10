"""Regression tests: _response_cache TTL eviction."""
import time
from unittest.mock import patch
import pytest


def test_cached_response_returned_within_ttl():
    """A cached entry is returned when it is younger than CACHE_TTL_SECONDS."""
    from src.llm_core import _set_cached_response, _get_cached_response, _response_cache
    _response_cache.clear()
    _set_cached_response("key1", "hello")
    result = _get_cached_response("key1")
    assert result == "hello", f"Expected 'hello', got {result!r}"


def test_cached_response_evicted_after_ttl():
    """A cached entry is evicted and None returned when it exceeds CACHE_TTL_SECONDS."""
    from src.llm_core import _set_cached_response, _get_cached_response, _response_cache, LLMConfig
    _response_cache.clear()
    _set_cached_response("key2", "stale-response")
    # Patch the stored_at timestamp to be older than TTL
    key, (resp, ts) = "key2", _response_cache["key2"]
    _response_cache["key2"] = (resp, time.time() - LLMConfig.CACHE_TTL_SECONDS - 1)
    result = _get_cached_response("key2")
    assert result is None, f"Expected None for expired entry, got {result!r}"


def test_expired_entry_removed_from_cache():
    """An expired entry must be removed from _response_cache on access."""
    from src.llm_core import _set_cached_response, _get_cached_response, _response_cache, LLMConfig
    _response_cache.clear()
    _set_cached_response("key3", "old")
    _response_cache["key3"] = (_response_cache["key3"][0], time.time() - LLMConfig.CACHE_TTL_SECONDS - 1)
    _get_cached_response("key3")
    assert "key3" not in _response_cache, "Expired entry must be removed from cache after read"


def test_cache_stores_tuple_with_timestamp():
    """_response_cache must store (response, float) tuples, not plain strings."""
    from src.llm_core import _set_cached_response, _response_cache
    _response_cache.clear()
    _set_cached_response("key4", "value")
    entry = _response_cache.get("key4")
    assert isinstance(entry, tuple), f"Expected tuple, got {type(entry)}"
    assert len(entry) == 2, f"Expected 2-tuple, got {entry!r}"
    assert entry[0] == "value", f"Expected 'value' in entry[0], got {entry[0]!r}"
    assert isinstance(entry[1], float), f"Expected float timestamp in entry[1], got {type(entry[1])}"
