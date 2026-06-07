import asyncio
import json
import sys
import threading
import time
import types

import pytest

from src.deep_research import DeepResearcher
from services.search import core as search_core
from services.search import providers as search_providers


class _ControlledResearcher(DeepResearcher):
    def __init__(self, *args, **kwargs):
        super().__init__(
            llm_endpoint="http://local.test/v1/chat/completions",
            llm_model="local-model",
            *args,
            **kwargs,
        )
        self.active = 0
        self.max_active = 0

    async def _search(self, query):
        return [
            {"url": f"https://example.test/{query}/{i}", "title": f"{query}-{i}"}
            for i in range(4)
        ]

    async def _fetch_and_extract(self, url, question, title):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return {"url": url, "title": title, "summary": "ok"}


@pytest.mark.asyncio
async def test_search_and_extract_respects_extraction_concurrency():
    researcher = _ControlledResearcher(extraction_concurrency=2, max_urls_per_round=4)
    researcher._start_time = time.time()

    findings = await researcher._search_and_extract(["a", "b"], "question")

    assert len(findings) == 8
    assert researcher.max_active == 2


@pytest.mark.asyncio
async def test_duckduckgo_only_research_searches_sequentially(monkeypatch):
    providers_mod = types.ModuleType("src.search.providers")
    providers_mod._get_search_settings = lambda: {
        "search_provider": "duckduckgo",
        "search_fallback_chain": ["duckduckgo"],
    }
    core_mod = types.ModuleType("src.search.core")
    core_mod._build_provider_chain = lambda provider: ["duckduckgo"]
    monkeypatch.setitem(sys.modules, "src.search.providers", providers_mod)
    monkeypatch.setitem(sys.modules, "src.search.core", core_mod)

    class _DuckDuckGoResearcher(DeepResearcher):
        def __init__(self):
            super().__init__(
                llm_endpoint="http://local.test/v1/chat/completions",
                llm_model="local-model",
                search_provider="duckduckgo",
                extraction_concurrency=2,
                max_urls_per_round=2,
            )
            self.active_searches = 0
            self.max_active_searches = 0
            self.search_events = []
            self.active_extracts = 0
            self.max_active_extracts = 0

        async def _search(self, query):
            self.search_events.append(f"start:{query}")
            self.active_searches += 1
            self.max_active_searches = max(self.max_active_searches, self.active_searches)
            await asyncio.sleep(0.01)
            self.active_searches -= 1
            self.search_events.append(f"end:{query}")
            return [
                {"url": f"https://example.test/{query}/{i}", "title": f"{query}-{i}"}
                for i in range(2)
            ]

        async def _fetch_and_extract(self, url, question, title):
            self.active_extracts += 1
            self.max_active_extracts = max(self.max_active_extracts, self.active_extracts)
            await asyncio.sleep(0.01)
            self.active_extracts -= 1
            return {"url": url, "title": title, "summary": "ok"}

    researcher = _DuckDuckGoResearcher()
    researcher._start_time = time.time()

    findings = await researcher._search_and_extract(["a", "b", "c"], "question")

    assert len(findings) == 6
    assert researcher.max_active_searches == 1
    assert researcher.search_events == [
        "start:a", "end:a",
        "start:b", "end:b",
        "start:c", "end:c",
    ]
    assert researcher.max_active_extracts == 2


@pytest.mark.asyncio
async def test_api_primary_duckduckgo_fallback_is_bounded(monkeypatch):
    chain = ["google_pse", "duckduckgo"]
    monkeypatch.setattr(
        search_providers,
        "_get_search_settings",
        lambda: {"search_provider": "google_pse", "search_fallback_chain": chain},
    )
    monkeypatch.setattr(search_core, "_build_provider_chain", lambda provider: list(chain))

    lock = threading.Lock()
    active_duckduckgo = 0
    max_active_duckduckgo = 0

    def fake_call_provider(provider, query, count, time_filter=None):
        nonlocal active_duckduckgo, max_active_duckduckgo
        if provider == "google_pse":
            return []
        if provider == "duckduckgo":
            with lock:
                active_duckduckgo += 1
                max_active_duckduckgo = max(max_active_duckduckgo, active_duckduckgo)
            time.sleep(0.02)
            with lock:
                active_duckduckgo -= 1
            return [{"url": f"https://example.test/{query}", "title": query}]
        return []

    monkeypatch.setattr(search_core, "_call_provider", fake_call_provider)

    class _FallbackResearcher(DeepResearcher):
        async def _fetch_and_extract(self, url, question, title):
            return {"url": url, "title": title, "summary": "ok"}

    researcher = _FallbackResearcher(
        llm_endpoint="http://local.test/v1/chat/completions",
        llm_model="local-model",
        search_provider="google_pse",
        max_urls_per_round=1,
        extraction_concurrency=3,
    )
    researcher._start_time = time.time()

    findings = await researcher._search_and_extract(["a", "b", "c"], "question")

    assert len(findings) == 3
    assert researcher.providers_used == ["duckduckgo"]
    assert max_active_duckduckgo == 1


@pytest.mark.asyncio
async def test_api_primary_remains_parallel_with_duckduckgo_fallback(monkeypatch):
    chain = ["google_pse", "duckduckgo"]
    monkeypatch.setattr(
        search_providers,
        "_get_search_settings",
        lambda: {
            "search_provider": "google_pse",
            "search_fallback_chain": chain,
            "google_pse_key": "test-key",
            "google_pse_cx": "test-cx",
        },
    )
    monkeypatch.setattr(search_core, "_build_provider_chain", lambda provider: list(chain))

    lock = threading.Lock()
    active_google = 0
    max_active_google = 0

    def fake_call_provider(provider, query, count, time_filter=None):
        nonlocal active_google, max_active_google
        if provider == "google_pse":
            with lock:
                active_google += 1
                max_active_google = max(max_active_google, active_google)
            time.sleep(0.02)
            with lock:
                active_google -= 1
            return [{"url": f"https://google.example/{query}", "title": query}]
        if provider == "duckduckgo":
            raise AssertionError("DuckDuckGo fallback should not run when API primary succeeds")
        return []

    monkeypatch.setattr(search_core, "_call_provider", fake_call_provider)

    class _ApiResearcher(DeepResearcher):
        async def _fetch_and_extract(self, url, question, title):
            return {"url": url, "title": title, "summary": "ok"}

    researcher = _ApiResearcher(
        llm_endpoint="http://local.test/v1/chat/completions",
        llm_model="local-model",
        search_provider="google_pse",
        max_urls_per_round=1,
        extraction_concurrency=3,
    )
    researcher._start_time = time.time()

    findings = await researcher._search_and_extract(["a", "b", "c"], "question")

    assert len(findings) == 3
    assert researcher.providers_used == ["google_pse"]
    assert max_active_google > 1


@pytest.mark.asyncio
async def test_fetch_and_extract_uses_configured_timeout(monkeypatch):
    captured = {}
    search_mod = types.ModuleType("src.search")

    def fake_fetch_webpage_content(url, timeout):
        return {
            "success": True,
            "content": "useful page content",
            "title": "Page",
            "og_image": "",
        }

    search_mod.fetch_webpage_content = fake_fetch_webpage_content
    monkeypatch.setitem(sys.modules, "src.search", search_mod)

    async def immediate_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", immediate_to_thread)

    researcher = DeepResearcher(
        llm_endpoint="http://local.test/v1/chat/completions",
        llm_model="local-model",
        extraction_timeout=123,
    )

    async def fake_llm(messages, temperature=0.3, max_tokens=4096, timeout=60):
        captured["timeout"] = timeout
        return json.dumps({
            "rational": "relevant",
            "evidence": "evidence",
            "summary": "useful page content",
        })

    researcher._llm = fake_llm

    result = await researcher._fetch_and_extract("https://example.test", "question", "Title")

    assert result["summary"] == "useful page content"
    assert captured["timeout"] == 123


def test_extraction_timeout_allows_long_local_model_runs():
    researcher = DeepResearcher(
        llm_endpoint="http://local.test/v1/chat/completions",
        llm_model="local-model",
        extraction_timeout=1800,
    )

    assert researcher.extraction_timeout == 1800


@pytest.mark.asyncio
async def test_planning_and_query_generation_use_configured_timeouts():
    researcher = DeepResearcher(
        llm_endpoint="http://local.test/v1/chat/completions",
        llm_model="local-model",
        planning_timeout=234,
        query_timeout=345,
    )
    captured = []

    async def fake_llm(messages, temperature=0.3, max_tokens=4096, timeout=60):
        captured.append(timeout)
        if max_tokens == 1024:
            return json.dumps({
                "sub_questions": ["one"],
                "key_topics": ["topic"],
                "success_criteria": "complete",
            })
        return json.dumps(["query one", "query two"])

    researcher._llm = fake_llm

    plan = await researcher._create_plan("question")
    queries = await researcher._generate_queries("question", "", 1)

    assert "Sub-questions: one" in plan
    assert queries == ["query one", "query two"]
    assert captured == [234, 345]
