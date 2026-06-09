import asyncio
import json
import sys
import time
import types

import pytest

from src.deep_research import DeepResearcher


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
