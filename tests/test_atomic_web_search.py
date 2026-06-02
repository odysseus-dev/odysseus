"""Tests for atomic_web_search function and tool handler.

Covers:
- atomic_web_search function (provider retry, fallback, time_filter, disabled)
- Tool handler JSON parsing (query, count, time_filter, citation_index)
- function_call_to_tool_block payload shape

NOTE: These tests use monkeypatch on services.search.core. Run with
test_search_module_consolidation.py in the same session, but be aware
that the consolidation tests should run first (alphabetical order handles this).
"""
import asyncio
import json
import sys
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# atomic_web_search function tests
# ---------------------------------------------------------------------------

class TestAtomicWebSearch:
    """Test atomic_web_search in services.search.core."""

    def test_returns_results_from_primary_provider(self, monkeypatch):
        from services.search.core import atomic_web_search
        hits = [{"title": "Hit", "url": "https://example.com", "snippet": "s"}]
        monkeypatch.setattr(
            sys.modules["services.search.core"],
            "_build_provider_chain", lambda provider: ["searxng"],
        )
        monkeypatch.setattr(
            sys.modules["services.search.core"],
            "_call_provider", lambda prov, query, count, tf: hits,
        )
        monkeypatch.setattr(
            sys.modules["services.search.core"],
            "_get_search_settings", lambda: {"search_provider": "searxng"},
        )
        result = atomic_web_search("test query", count=3)
        assert result == hits

    def test_falls_back_to_secondary_provider_on_empty(self, monkeypatch):
        from services.search.core import atomic_web_search
        calls = []

        def _call(prov, query, count, tf):
            calls.append(prov)
            return [] if prov == "searxng" else [{"title": "Fallback", "url": "https://ddg.example", "snippet": "s"}]

        monkeypatch.setattr(
            sys.modules["services.search.core"],
            "_build_provider_chain", lambda provider: ["searxng", "duckduckgo"],
        )
        monkeypatch.setattr(
            sys.modules["services.search.core"],
            "_call_provider", _call,
        )
        monkeypatch.setattr(
            sys.modules["services.search.core"],
            "_get_search_settings", lambda: {"search_provider": "searxng"},
        )
        result = atomic_web_search("test")
        assert len(result) == 1
        assert result[0]["title"] == "Fallback"
        assert calls == ["searxng", "searxng", "duckduckgo"]

    def test_returns_empty_when_all_providers_fail(self, monkeypatch):
        from services.search.core import atomic_web_search
        monkeypatch.setattr(
            sys.modules["services.search.core"],
            "_build_provider_chain", lambda provider: ["searxng", "duckduckgo"],
        )
        monkeypatch.setattr(
            sys.modules["services.search.core"],
            "_call_provider", lambda prov, query, count, tf: [],
        )
        monkeypatch.setattr(
            sys.modules["services.search.core"],
            "_get_search_settings", lambda: {"search_provider": "searxng"},
        )
        result = atomic_web_search("impossible query")
        assert result == []

    def test_returns_empty_when_provider_disabled(self, monkeypatch):
        from services.search.core import atomic_web_search
        monkeypatch.setattr(
            sys.modules["services.search.core"],
            "_get_search_settings", lambda: {"search_provider": "disabled"},
        )
        result = atomic_web_search("test")
        assert result == []

    def test_passes_time_filter_to_provider(self, monkeypatch):
        from services.search.core import atomic_web_search
        received_tf = []

        def _call(prov, query, count, tf):
            received_tf.append(tf)
            return [{"title": "T", "url": "https://example.com", "snippet": "s"}]

        monkeypatch.setattr(
            sys.modules["services.search.core"],
            "_build_provider_chain", lambda provider: ["searxng"],
        )
        monkeypatch.setattr(sys.modules["services.search.core"], "_call_provider", _call)
        monkeypatch.setattr(
            sys.modules["services.search.core"],
            "_get_search_settings", lambda: {"search_provider": "searxng"},
        )
        atomic_web_search("news", time_filter="week")
        assert received_tf == ["week"]

    def test_retries_failed_provider_twice(self, monkeypatch):
        from services.search.core import atomic_web_search
        from services.search.analytics import NetworkError
        calls = []

        def _call(prov, query, count, tf):
            calls.append(prov)
            raise NetworkError("timeout")

        monkeypatch.setattr(
            sys.modules["services.search.core"],
            "_build_provider_chain", lambda provider: ["searxng", "duckduckgo"],
        )
        monkeypatch.setattr(sys.modules["services.search.core"], "_call_provider", _call)
        monkeypatch.setattr(
            sys.modules["services.search.core"],
            "_get_search_settings", lambda: {"search_provider": "searxng"},
        )
        result = atomic_web_search("test")
        assert result == []
        assert calls == ["searxng", "searxng", "duckduckgo", "duckduckgo"]


# ---------------------------------------------------------------------------
# Tool handler tests (_direct_fallback web_search path)
# ---------------------------------------------------------------------------

class TestWebSearchToolHandler:
    """Test the web_search handler in _direct_fallback."""

    @pytest.mark.asyncio
    async def test_parses_json_args_with_time_filter(self, monkeypatch):
        hits = [{"title": "T", "url": "https://example.com", "snippet": "s"}]
        monkeypatch.setattr(
            sys.modules["services.search.core"],
            "atomic_web_search",
            lambda q, count=5, time_filter=None: hits,
        )

        # Force re-import so tool_execution picks up the patched module
        if "src.search" in sys.modules:
            del sys.modules["src.search"]
        if "src.tool_execution" in sys.modules:
            del sys.modules["src.tool_execution"]

        from src.tool_execution import _direct_fallback
        content = json.dumps({"query": "test", "count": 3, "time_filter": "day"})
        result = await _direct_fallback("web_search", content)

        assert result["exit_code"] == 0
        output = json.loads(result["output"])
        assert len(output) == 1
        assert output[0]["index"] == 1
        assert "sources" in result
        assert result["sources"][0]["index"] == 1

    @pytest.mark.asyncio
    async def test_plain_text_query_works(self, monkeypatch):
        hits = [{"title": "T", "url": "https://example.com", "snippet": "s"}]
        monkeypatch.setattr(
            sys.modules["services.search.core"],
            "atomic_web_search",
            lambda q, count=5, time_filter=None: hits,
        )

        if "src.search" in sys.modules:
            del sys.modules["src.search"]
        if "src.tool_execution" in sys.modules:
            del sys.modules["src.tool_execution"]

        from src.tool_execution import _direct_fallback
        result = await _direct_fallback("web_search", "simple query")
        assert result["exit_code"] == 0
        output = json.loads(result["output"])
        assert len(output) == 1

    @pytest.mark.asyncio
    async def test_empty_results_return_empty_json_array(self, monkeypatch):
        monkeypatch.setattr(
            sys.modules["services.search.core"],
            "atomic_web_search",
            lambda q, count=5, time_filter=None: [],
        )

        if "src.search" in sys.modules:
            del sys.modules["src.search"]
        if "src.tool_execution" in sys.modules:
            del sys.modules["src.tool_execution"]

        from src.tool_execution import _direct_fallback
        result = await _direct_fallback("web_search", "impossible query")
        assert result["exit_code"] == 0
        assert json.loads(result["output"]) == []
        assert result["sources"] == []

    @pytest.mark.asyncio
    async def test_citation_index_offset_works(self, monkeypatch):
        hits = [
            {"title": "T1", "url": "https://a.com", "snippet": "s"},
            {"title": "T2", "url": "https://b.com", "snippet": "s"},
        ]
        monkeypatch.setattr(
            sys.modules["services.search.core"],
            "atomic_web_search",
            lambda q, count=5, time_filter=None: hits,
        )

        if "src.search" in sys.modules:
            del sys.modules["src.search"]
        if "src.tool_execution" in sys.modules:
            del sys.modules["src.tool_execution"]

        from src.tool_execution import _direct_fallback
        result = await _direct_fallback("web_search", "query", citation_index=5)
        output = json.loads(result["output"])
        assert output[0]["index"] == 5
        assert output[1]["index"] == 6
        assert result["sources"][0]["index"] == 5
        assert result["sources"][1]["index"] == 6

    @pytest.mark.asyncio
    async def test_invalid_time_filter_is_ignored(self, monkeypatch):
        """Invalid time_filter values should be treated as None."""
        received_tf = []

        def _atomic(q, count=5, time_filter=None):
            received_tf.append(time_filter)
            return [{"title": "T", "url": "https://example.com", "snippet": "s"}]

        monkeypatch.setattr(sys.modules["services.search.core"], "atomic_web_search", _atomic)

        if "src.search" in sys.modules:
            del sys.modules["src.search"]
        if "src.tool_execution" in sys.modules:
            del sys.modules["src.tool_execution"]

        from src.tool_execution import _direct_fallback
        content = json.dumps({"query": "test", "time_filter": "invalid_value"})
        result = await _direct_fallback("web_search", content)
        assert result["exit_code"] == 0
        assert received_tf == [None]
