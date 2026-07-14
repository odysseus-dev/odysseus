"""Tests for operator_research fan-out and the Perplexity provider."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _research_available():
    from services.operator import core
    core._status_cache[core.CAP_RESEARCH] = (time.monotonic(), {"available": True})
    yield
    core.reset_status_cache()


# ── URL normalization / merge ──

def test_normalize_url_strips_www_scheme_slash_fragment():
    from services.operator.research import _normalize_url

    a = _normalize_url("https://www.Example.com/path/")
    b = _normalize_url("http://example.com/path")
    c = _normalize_url("https://example.com/path#section")
    assert a == b == c


def test_merge_dedupes_and_records_also_from():
    from services.operator.research import _merge

    per_provider = {
        "tinyfish": [{"title": "A", "url": "https://x.com/a"}, {"title": "B", "url": "https://x.com/b"}],
        "perplexity": [{"title": "A2", "url": "https://www.x.com/a"}],
    }
    merged = _merge(per_provider)
    urls = [m["url"] for m in merged]
    assert "https://x.com/a" in urls
    assert "https://x.com/b" in urls
    assert len(merged) == 2  # the duplicate 'a' collapsed
    a_entry = next(m for m in merged if "/a" in m["url"])
    assert a_entry["source"] == "tinyfish"
    assert "perplexity" in a_entry.get("also_from", [])


def test_merge_interleaves_by_rank():
    from services.operator.research import _merge

    per_provider = {
        "tinyfish": [{"url": "https://t1"}, {"url": "https://t2"}],
        "firecrawl": [{"url": "https://f1"}],
    }
    merged = _merge(per_provider)
    # Rank-0 round-robin: t1, f1 come before t2.
    urls = [m["url"] for m in merged]
    assert urls.index("https://f1") < urls.index("https://t2")


# ── fan-out orchestration ──

def _patch_providers(monkeypatch, configured, fns):
    monkeypatch.setattr("services.operator.research._configured_providers", lambda: configured)
    monkeypatch.setattr("services.operator.research._provider_fns", lambda: fns)


def test_fanout_all_providers(monkeypatch):
    from services.operator.research import operator_research

    fns = {
        "tinyfish": lambda q, c: [{"title": "T", "url": "https://t.com/x"}],
        "perplexity": lambda q, c: [{"title": "P", "url": "https://p.com/y"}],
        "firecrawl": lambda q, c: [{"title": "F", "url": "https://f.com/z"}],
    }
    _patch_providers(monkeypatch, ["tinyfish", "perplexity", "firecrawl"], fns)

    result = operator_research("agentic os")
    assert result["ok"] is True
    assert result["data"]["result_count"] == 3
    assert set(result["data"]["providers_used"]) == {"tinyfish", "perplexity", "firecrawl"}
    assert result["data"]["providers_skipped"] == []


def test_fanout_skips_unconfigured(monkeypatch):
    from services.operator.research import operator_research

    fns = {
        "tinyfish": lambda q, c: [{"url": "https://t.com/x"}],
        "perplexity": lambda q, c: [{"url": "https://p.com/y"}],
        "firecrawl": lambda q, c: [{"url": "https://f.com/z"}],
    }
    _patch_providers(monkeypatch, ["tinyfish", "perplexity"], fns)

    result = operator_research("q")
    assert result["ok"] is True
    assert "firecrawl" in result["data"]["providers_skipped"]
    assert set(result["data"]["providers_used"]) == {"tinyfish", "perplexity"}


def test_fanout_isolates_provider_error(monkeypatch):
    from services.operator.research import operator_research

    def boom(q, c):
        raise RuntimeError("provider exploded")

    fns = {
        "tinyfish": lambda q, c: [{"url": "https://t.com/x"}],
        "perplexity": boom,
        "firecrawl": lambda q, c: [{"url": "https://f.com/z"}],
    }
    _patch_providers(monkeypatch, ["tinyfish", "perplexity", "firecrawl"], fns)

    result = operator_research("q")
    assert result["ok"] is True  # survivors still return
    assert result["degraded"] is True
    assert "provider exploded" in result["data"]["provider_errors"]["perplexity"]
    assert set(result["data"]["providers_used"]) == {"tinyfish", "firecrawl"}


def test_fanout_deadline_marks_pending_timeout(monkeypatch):
    from services.operator.research import operator_research

    def slow(q, c):
        time.sleep(2)
        return [{"url": "https://slow.com"}]

    fns = {
        "tinyfish": lambda q, c: [{"url": "https://fast.com/x"}],
        "perplexity": slow,
        "firecrawl": lambda q, c: [{"url": "https://f.com/z"}],
    }
    _patch_providers(monkeypatch, ["tinyfish", "perplexity", "firecrawl"], fns)

    result = operator_research("q", deadline=0.3)
    assert result["data"]["provider_errors"].get("perplexity") == "timeout"
    assert "tinyfish" in result["data"]["providers_used"]


def test_fanout_all_fail_is_not_ok(monkeypatch):
    from services.operator.research import operator_research

    def boom(q, c):
        raise RuntimeError("down")

    _patch_providers(monkeypatch, ["tinyfish"], {"tinyfish": boom})
    result = operator_research("q")
    assert result["ok"] is False
    assert result["reason"] == "all_providers_failed"


def test_fanout_no_providers_configured(monkeypatch):
    from services.operator.research import operator_research

    _patch_providers(monkeypatch, [], {})
    result = operator_research("q")
    assert result["ok"] is False
    assert result["reason"] == "no_providers"


def test_empty_query_rejected():
    from services.operator.research import operator_research
    result = operator_research("   ")
    assert result["ok"] is False
    assert result["reason"] == "empty_query"


# ── Perplexity provider ──

def test_perplexity_registered_in_provider_info():
    from services.search.providers import PROVIDER_INFO
    assert "perplexity" in PROVIDER_INFO
    label, needs_key, needs_url = PROVIDER_INFO["perplexity"]
    assert needs_key is True
    assert needs_url is False


def test_perplexity_search_parses_answer_and_citations(monkeypatch):
    from services.search import providers

    monkeypatch.setattr(providers, "_get_provider_key", lambda p: "test-key")

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [{"message": {"content": "Agentic OS is X."}}],
                "search_results": [
                    {"title": "Source One", "url": "https://a.test", "snippet": "s1"},
                    {"title": "Source Two", "url": "https://b.test", "snippet": "s2"},
                ],
            }

    monkeypatch.setattr(providers.httpx, "post", lambda *a, **k: _Resp())
    results = providers.perplexity_search("agentic os", count=5)

    assert len(results) == 2
    assert results[0]["url"] == "https://a.test"
    assert "[Perplexity answer] Agentic OS is X." in results[0]["snippet"]


def test_perplexity_search_no_key_returns_empty(monkeypatch):
    from services.search import providers

    monkeypatch.setattr(providers, "_get_provider_key", lambda p: "")
    monkeypatch.setattr(providers.os, "environ", {})
    assert providers.perplexity_search("q") == []


def test_perplexity_in_core_dispatch():
    import services.search.core as core
    with patch.object(core, "perplexity_search", return_value=[{"url": "https://x"}]) as pk:
        out = core._call_provider("perplexity", "q", 5, None)
    assert out == [{"url": "https://x"}]
    pk.assert_called_once()


# ── tool registration ──

def test_operator_research_registered():
    from src.agent_tools import TOOL_TAGS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    names = {t["function"]["name"] for t in FUNCTION_TOOL_SCHEMAS}
    assert "operator_research" in names
    assert "operator_research" in TOOL_TAGS


def test_do_operator_research_requires_query():
    from src.tool_implementations import do_operator_research
    result = asyncio.run(do_operator_research("{}"))
    assert result["exit_code"] == 1
