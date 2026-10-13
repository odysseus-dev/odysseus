import json

import pytest

from src.deep_research import DeepResearcher
from src.research_navigator import ResearchNavigator, ResearchPage


class _LoopNavigator(ResearchNavigator):
    def __init__(self):
        super().__init__()
        self.searches = []
        self.fetches = []
        self.browser_reads = []

    async def search(self, query: str, *, count: int = 10):
        self.searches.append(query)
        if query == "official docs":
            return [{
                "url": "https://docs.example.com/tool/release-notes",
                "title": "Official release notes",
                "snippet": "Official documentation.",
            }]
        return [{
            "url": "https://example.com/app",
            "title": "App page",
            "snippet": "Interactive app page.",
        }]

    async def fetch(self, url: str, *, timeout: int = 10, max_bytes: int | None = None) -> ResearchPage:
        self.fetches.append(url)
        if "docs.example.com" in url:
            return ResearchPage(
                url=url,
                title="Official release notes",
                content="Official documentation with concrete facts about the question.",
                success=True,
                retrieval="fetch",
            )
        return ResearchPage(
            url=url,
            title="App page",
            content="Cookie banner Navigation Sign in Search Menu",
            success=True,
            retrieval="fetch",
        )

    async def browser_read(self, url: str, *, timeout: int = 45) -> ResearchPage:
        self.browser_reads.append(url)
        return ResearchPage(
            url=url,
            title="Rendered app page",
            content="Browser-rendered evidence with concrete facts about the question.",
            success=True,
            retrieval="browser",
        )


@pytest.mark.asyncio
async def test_research_loop_continues_past_weak_fetch_and_uses_browser():
    nav = _LoopNavigator()
    researcher = DeepResearcher(
        llm_endpoint="http://local.test/v1/chat/completions",
        llm_model="local-model",
        max_rounds=3,
        min_rounds=1,
        max_urls_per_round=1,
        extraction_concurrency=1,
    )
    researcher.navigator = nav
    llm_calls = {"actions": 0}

    async def _fake_llm(messages, **kwargs):
        prompt = messages[0]["content"]
        if "You are a research strategist" in prompt:
            return json.dumps({
                "sub_questions": ["Find rendered evidence", "Verify official source"],
                "key_topics": ["rendered page", "official docs"],
                "success_criteria": "Use rendered and official evidence.",
            })
        if "Classify this research question" in prompt:
            return "general"
        if "You are controlling a bounded research navigator" in prompt:
            llm_calls["actions"] += 1
            query = "interactive app page" if llm_calls["actions"] == 1 else "official docs"
            return json.dumps({"actions": [{"tool": "web_search", "query": query}]})
        if "You are updating an evolving research report" in prompt:
            return "Synthesized report with gathered facts."
        if "You are deciding whether a research report is comprehensive enough" in prompt:
            return "YES — rendered and official evidence are both present."
        if "Write a **long, detailed, comprehensive** research report" in prompt:
            return "Final report with rendered and official evidence."

        content = messages[1]["content"]
        if "Cookie banner" in content:
            return json.dumps({
                "rational": "boilerplate",
                "summary": "No relevant information found.",
                "evidence": "",
            })
        if "Browser-rendered evidence" in content:
            return json.dumps({
                "rational": "rendered evidence",
                "summary": "Useful browser-rendered evidence.",
                "evidence": "Browser-rendered evidence with concrete facts.",
            })
        return json.dumps({
            "rational": "official evidence",
            "summary": "Useful official evidence.",
            "evidence": "Official documentation with concrete facts.",
        })

    researcher._llm = _fake_llm

    result = await researcher.research("Research the app and verify with official docs.")

    assert result == "Final report with rendered and official evidence."
    assert nav.searches == ["interactive app page", "official docs"]
    assert nav.browser_reads == ["https://example.com/app"]
    assert researcher.round_count == 2
    assert [a["query"] for a in researcher.action_trace] == [
        "interactive app page",
        "official docs",
    ]
    assert any(f["retrieval"] == "browser" for f in researcher.findings)
    assert any(f["source_kind"] == "primary" for f in researcher.findings)
