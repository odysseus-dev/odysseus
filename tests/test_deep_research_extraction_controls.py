import asyncio
import json
import time

import pytest

from src.deep_research import DeepResearcher
from src.research_navigator import ResearchPage


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
async def test_search_and_extract_tracks_all_urls_selected_for_analysis():
    researcher = _ControlledResearcher(extraction_concurrency=2, max_urls_per_round=2)
    researcher._start_time = time.time()

    findings = await researcher._search_and_extract(["a"], "question")

    assert len(findings) == 2
    assert [
        {"url": item["url"], "title": item["title"]}
        for item in researcher.analyzed_urls
    ] == [
        {"url": "https://example.test/a/0", "title": "a-0"},
        {"url": "https://example.test/a/1", "title": "a-1"},
    ]
    assert all(item["requested_by"] == "web_search" for item in researcher.analyzed_urls)


@pytest.mark.asyncio
async def test_url_limit_balances_queries_within_the_existing_round_cap():
    researcher = _ControlledResearcher(max_urls_per_round=3)
    researcher._start_time = time.time()

    findings = await researcher._search_and_extract(["a", "b", "c", "d"], "question")

    assert len(findings) == 12
    assert len(researcher.urls_fetched) == 12
    assert [finding["title"] for finding in findings] == [
        f"{query}-{index}" for index in range(3) for query in ("a", "b", "c", "d")
    ]
    assert [{"url": item["url"], "title": item["title"]} for item in researcher.analyzed_urls[:4]] == [
        {"url": f"https://example.test/{query}/0", "title": f"{query}-0"}
        for query in ("a", "b", "c", "d")
    ]


@pytest.mark.asyncio
async def test_url_limit_counts_only_new_unique_urls_across_queries():
    researcher = _ControlledResearcher(max_urls_per_round=1)
    researcher._start_time = time.time()
    researcher.urls_fetched.add("https://example.test/already-read")

    async def search(query):
        urls = {
            "a": ["already-read", "shared", "shared", ""],
            "b": ["shared", "second", "third", "extra"],
            "c": ["later"],
        }[query]
        return [{"url": f"https://example.test/{url}" if url else "", "title": url} for url in urls]

    researcher._search = search

    findings = await researcher._search_and_extract(["a", "b", "c"], "question")

    assert [finding["title"] for finding in findings] == ["shared", "second", "later"]
    assert [item["title"] for item in researcher.analyzed_urls] == ["shared", "second", "later"]
    assert "https://example.test/extra" not in researcher.urls_fetched
    assert "https://example.test/third" not in researcher.urls_fetched


@pytest.mark.asyncio
async def test_balanced_selection_reuses_capacity_from_empty_or_failed_queries():
    researcher = _ControlledResearcher(max_urls_per_round=1)
    researcher._start_time = time.time()

    async def search(query):
        if query == "empty":
            return []
        if query == "failed":
            raise RuntimeError("fixture search failure")
        return [{"url": f"https://example.test/useful/{i}", "title": str(i)} for i in range(6)]

    researcher._search = search
    findings = await researcher._search_and_extract(["empty", "failed", "useful"], "question")

    assert [finding["title"] for finding in findings] == ["0", "1", "2"]
    assert len(researcher.urls_fetched) == 3


@pytest.mark.asyncio
async def test_fetch_and_extract_uses_configured_timeout(monkeypatch):
    captured = {}

    researcher = DeepResearcher(
        llm_endpoint="http://local.test/v1/chat/completions",
        llm_model="local-model",
        extraction_timeout=123,
    )

    async def fake_fetch(url, timeout=10):
        return ResearchPage(
            url=url,
            title="Page",
            content="useful page content",
            success=True,
            retrieval="fetch",
        )

    researcher.navigator.fetch = fake_fetch

    async def fake_llm(messages, temperature=0.3, max_tokens=4096, timeout=60, enable_thinking=None):
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

    async def fake_llm(messages, temperature=0.3, max_tokens=4096, timeout=60, enable_thinking=None):
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
