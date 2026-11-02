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
async def test_fetch_and_extract_uses_configured_timeout(monkeypatch):
    captured = {}

    researcher = DeepResearcher(
        llm_endpoint="http://local.test/v1/chat/completions",
        llm_model="local-model",
        extraction_timeout=123,
        helper_max_tokens=6789,
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

    async def fake_llm(messages, temperature=0.3, max_tokens=4096, timeout=300):
        captured["timeout"] = timeout
        captured["max_tokens"] = max_tokens
        return json.dumps({
            "rational": "relevant",
            "evidence": "evidence",
            "summary": "useful page content",
        })

    researcher._llm = fake_llm

    result = await researcher._fetch_and_extract("https://example.test", "question", "Title")

    assert result["summary"] == "useful page content"
    assert captured == {"timeout": 123, "max_tokens": 6789}


def test_extraction_timeout_allows_long_local_model_runs():
    researcher = DeepResearcher(
        llm_endpoint="http://local.test/v1/chat/completions",
        llm_model="local-model",
        extraction_timeout=1800,
    )

    assert researcher.extraction_timeout == 1800


@pytest.mark.asyncio
async def test_planning_and_query_generation_use_configured_limits():
    researcher = DeepResearcher(
        llm_endpoint="http://local.test/v1/chat/completions",
        llm_model="local-model",
        planning_timeout=234,
        query_timeout=345,
    )
    captured = []

    async def fake_llm(messages, temperature=0.3, max_tokens=4096, timeout=300):
        captured.append((max_tokens, timeout))
        if "research strategist" in messages[0]["content"]:
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
    assert captured == [(4096, 234), (4096, 345)]


@pytest.mark.asyncio
async def test_helper_token_budget_applies_to_plan_query_and_actions(monkeypatch):
    import src.settings as settings_mod

    monkeypatch.setattr(
        settings_mod,
        "get_setting",
        lambda key, default=None: default,
    )
    researcher = DeepResearcher(
        llm_endpoint="http://local.test/v1/chat/completions",
        llm_model="local-model",
        planning_timeout=234,
        query_timeout=345,
        helper_max_tokens=6144,
    )
    captured = []

    async def fake_llm(messages, temperature=0.3, max_tokens=4096, timeout=300):
        captured.append((max_tokens, timeout))
        if "research strategist" in messages[0]["content"]:
            return json.dumps({"sub_questions": ["one"]})
        if "search queries" in messages[0]["content"]:
            return json.dumps(["query one", "query two"])
        return "[]"

    researcher._llm = fake_llm

    await researcher._create_plan("question")
    await researcher._generate_queries("question", "", 1)
    await researcher._plan_research_actions("question", "", 1)

    assert captured == [
        (6144, 234),
        (6144, 345),
        (6144, 345),
    ]


def test_zero_max_time_disables_engine_cap():
    researcher = DeepResearcher(
        llm_endpoint="http://local.test/v1/chat/completions",
        llm_model="local-model",
        max_time=0,
    )
    researcher._start_time = time.time() - 10_000

    assert researcher._time_exceeded() is False


def test_reasoning_friendly_research_defaults():
    from src.settings import DEFAULT_SETTINGS

    assert DEFAULT_SETTINGS["research_helper_max_tokens"] == 4096
    assert DEFAULT_SETTINGS["research_planning_timeout_seconds"] >= 300
    assert DEFAULT_SETTINGS["research_query_timeout_seconds"] >= 300
    assert DEFAULT_SETTINGS["research_extraction_timeout_seconds"] >= 300
