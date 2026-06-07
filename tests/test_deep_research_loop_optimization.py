import asyncio
import time

import pytest

import src.deep_research as deep_research
from src.deep_research import DeepResearcher
from src.research_handler import ResearchHandler


class _LoopResearcher(DeepResearcher):
    def __init__(self, **kwargs):
        self.stop_after_round = kwargs.pop("stop_after_round", None)
        self.timeout_on_check = kwargs.pop("timeout_on_check", None)
        super().__init__(
            llm_endpoint="http://local.test/v1/chat/completions",
            llm_model="local-model",
            **kwargs,
        )
        self.time_checks = 0

    async def _create_plan(self, question):
        return "plan"

    async def _classify_category(self, question):
        return None

    async def _generate_queries(self, question, report, round_num):
        if round_num == 1:
            queries = [f"broad {i}" for i in range(4)]
        else:
            queries = [f"followup {round_num}-{i}" for i in range(3)]
        self.queries_used.update(queries)
        return queries

    async def _search_and_extract(self, queries, question):
        return [{"url": f"https://example.test/{len(self.findings)}", "summary": "ok"}]

    async def _synthesize(self, question, findings, current_report):
        return "updated report"

    async def _should_stop(self, question, report, round_num):
        return self.stop_after_round == round_num

    async def _final_report(self, question, report):
        return f"final: {report}"

    def _time_exceeded(self):
        self.time_checks += 1
        return self.timeout_on_check == self.time_checks


def test_stop_prompt_treats_max_rounds_as_safety_cap_not_target():
    assert "safety cap" in deep_research.STOP_PROMPT.lower()
    assert "below the target" not in deep_research.STOP_PROMPT.lower()


@pytest.mark.asyncio
async def test_auto_mode_stops_after_llm_says_enough_at_round_two():
    researcher = _LoopResearcher(max_rounds=20, min_rounds=2, stop_after_round=2)

    report = await researcher.research("question")
    stats = researcher.get_stats()

    assert report == "final: updated report"
    assert stats["Rounds"] == 2
    assert stats["Queries"] == 7
    assert stats["Stop Reason"] == "llm_stop"


@pytest.mark.asyncio
async def test_timeout_before_next_round_does_not_increment_round_count():
    researcher = _LoopResearcher(max_rounds=20, min_rounds=2, timeout_on_check=3)

    await researcher.research("question")
    stats = researcher.get_stats()

    assert stats["Rounds"] == 2
    assert stats["Queries"] == 7
    assert stats["Stop Reason"] == "time_budget"


@pytest.mark.asyncio
async def test_short_direct_time_budget_can_still_enter_first_round():
    researcher = _LoopResearcher(
        max_rounds=1,
        min_rounds=1,
        max_time=1,
        stop_after_round=1,
    )

    report = await researcher.research("question")
    stats = researcher.get_stats()

    assert report == "final: updated report"
    assert stats["Rounds"] == 1
    assert stats["Stop Reason"] == "llm_stop"


@pytest.mark.asyncio
async def test_research_resets_run_scoped_provider_state_when_reused():
    researcher = _LoopResearcher(max_rounds=1, min_rounds=1, stop_after_round=1)
    researcher.providers_used = ["duckduckgo"]
    researcher._last_search_error = "old provider error"

    await researcher.research("question")

    assert researcher.providers_used == []
    assert getattr(researcher, "_last_search_error", None) is None


@pytest.mark.asyncio
async def test_search_and_extract_global_url_cap_and_success_stats():
    class _UrlAccountingResearcher(DeepResearcher):
        def __init__(self):
            super().__init__(
                llm_endpoint="http://local.test/v1/chat/completions",
                llm_model="local-model",
                max_urls_per_round=2,
                extraction_concurrency=4,
            )
            self.fetch_attempt_urls = []

        async def _search(self, query):
            return [
                {"url": f"https://example.test/{query}/{i}", "title": f"{query}-{i}"}
                for i in range(4)
            ]

        async def _fetch_and_extract(self, url, question, title):
            self.fetch_attempt_urls.append(url)
            if url.endswith("/3"):
                return None
            return {"url": url, "title": title, "summary": "ok"}

    researcher = _UrlAccountingResearcher()
    researcher._start_time = time.time()

    findings = await researcher._search_and_extract(["a", "b"], "question")
    stats = researcher.get_stats()

    assert len(researcher.fetch_attempt_urls) == 4
    assert len(findings) == 3
    assert stats["URL Candidates"] == 8
    assert stats["Fetch Attempts"] == 4
    assert stats["URLs"] == 3


def test_call_research_service_auto_rounds_uses_safety_cap_and_min_two(monkeypatch):
    captured = {}

    class _FakeResearcher:
        findings = []

        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def research(self, *args, **kwargs):
            return "report"

        def get_stats(self):
            return {"Duration": "1.0s", "Rounds": 2, "Queries": 7, "URLs": 3}

    monkeypatch.setattr(ResearchHandler, "_probe_endpoint", lambda *a, **k: _async_none())
    monkeypatch.setattr(deep_research, "DeepResearcher", _FakeResearcher)

    handler = ResearchHandler.__new__(ResearchHandler)
    result = asyncio.run(handler.call_research_service(
        "question",
        "http://local.test/v1/chat/completions",
        "local-model",
        max_rounds=0,
    ))

    assert "report" in result
    assert captured["max_rounds"] == 20
    assert captured["min_rounds"] == 2


def test_call_research_service_explicit_one_round_sets_min_one(monkeypatch):
    captured = {}

    class _FakeResearcher:
        findings = []

        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def research(self, *args, **kwargs):
            return "report"

        def get_stats(self):
            return {"Duration": "1.0s", "Rounds": 1, "Queries": 4, "URLs": 3}

    monkeypatch.setattr(ResearchHandler, "_probe_endpoint", lambda *a, **k: _async_none())
    monkeypatch.setattr(deep_research, "DeepResearcher", _FakeResearcher)

    handler = ResearchHandler.__new__(ResearchHandler)
    asyncio.run(handler.call_research_service(
        "question",
        "http://local.test/v1/chat/completions",
        "local-model",
        max_rounds=1,
    ))

    assert captured["max_rounds"] == 1
    assert captured["min_rounds"] == 1


async def _async_none():
    return None
