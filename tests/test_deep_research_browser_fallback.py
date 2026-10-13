import asyncio
import json

from src.deep_research import DeepResearcher
from src.research_navigator import ResearchNavigator, ResearchPage


class _FallbackNavigator(ResearchNavigator):
    def __init__(self):
        super().__init__()
        self.browser_called = False

    async def fetch(self, url: str, *, timeout: int = 10, max_bytes: int | None = None) -> ResearchPage:
        return ResearchPage(url=url, success=False, error="no readable text content")

    async def browser_read(self, url: str, *, timeout: int = 45) -> ResearchPage:
        self.browser_called = True
        return ResearchPage(
            url=url,
            title="Browser page",
            content="Browser-only evidence about the question.",
            success=True,
            retrieval="browser",
        )


class _WeakFetchNavigator(ResearchNavigator):
    def __init__(self):
        super().__init__()
        self.fetch_called = False
        self.browser_called = False

    async def fetch(self, url: str, *, timeout: int = 10, max_bytes: int | None = None) -> ResearchPage:
        self.fetch_called = True
        return ResearchPage(
            url=url,
            title="Weak fetch page",
            content="Cookie banner Navigation Sign in Search Menu",
            success=True,
            retrieval="fetch",
        )

    async def browser_read(self, url: str, *, timeout: int = 45) -> ResearchPage:
        self.browser_called = True
        return ResearchPage(
            url=url,
            title="Browser page",
            content="Browser-rendered evidence about the actual question.",
            success=True,
            retrieval="browser",
        )


class _FailingBrowserNavigator(ResearchNavigator):
    def __init__(self):
        super().__init__()
        self.browser_called = False

    async def fetch(self, url: str, *, timeout: int = 10, max_bytes: int | None = None) -> ResearchPage:
        return ResearchPage(url=url, success=False, error="fetch blocked")

    async def browser_read(self, url: str, *, timeout: int = 45) -> ResearchPage:
        self.browser_called = True
        return ResearchPage(
            url=url,
            title="Blocked browser page",
            content="",
            success=False,
            retrieval="browser",
            error="bot check",
        )


def _researcher_for_fetch(nav: ResearchNavigator) -> DeepResearcher:
    researcher = DeepResearcher.__new__(DeepResearcher)
    researcher.max_content_chars = 15000
    researcher.extraction_timeout = 30
    researcher.search_provider_override = None
    researcher.navigator = nav
    researcher.urls_fetched = set()
    researcher.analyzed_urls = [{"url": "https://example.com/app", "title": "Example App"}]
    researcher._progress = None
    return researcher


def test_fetch_and_extract_uses_private_browser_when_fetch_has_no_text(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: True)

    nav = _FallbackNavigator()
    researcher = _researcher_for_fetch(nav)

    async def _fake_llm(messages, **kwargs):
        assert "Browser-only evidence" in messages[1]["content"]
        return json.dumps({
            "rational": "browser fallback",
            "summary": "Useful browser-only evidence.",
            "evidence": "Browser-only evidence about the question.",
        })

    researcher._llm = _fake_llm

    finding = asyncio.run(researcher._fetch_and_extract(
        "https://example.com/app",
        "What does the app do?",
        "Example App",
    ))

    assert nav.browser_called is True
    assert finding["retrieval"] == "browser"
    assert finding["summary"] == "Useful browser-only evidence."
    assert researcher.analyzed_urls[0]["retrieval"] == "browser"


def test_fetch_and_extract_respects_browser_fallback_setting(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: False)

    nav = _FallbackNavigator()
    researcher = _researcher_for_fetch(nav)

    async def _fake_llm(messages, **kwargs):
        raise AssertionError("LLM extraction should not run without readable content")

    researcher._llm = _fake_llm

    finding = asyncio.run(researcher._fetch_and_extract(
        "https://example.com/app",
        "What does the app do?",
        "Example App",
    ))

    assert finding is None
    assert nav.browser_called is False


def test_browser_fallback_failure_is_visible_to_next_planner(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: True)

    nav = _FailingBrowserNavigator()
    researcher = _researcher_for_fetch(nav)

    async def _fake_llm(messages, **kwargs):
        raise AssertionError("LLM extraction should not run without readable content")

    researcher._llm = _fake_llm

    finding = asyncio.run(researcher._fetch_and_extract(
        "https://example.com/app",
        "What does the app do?",
        "Example App",
    ))

    assert finding is None
    assert nav.browser_called is True
    assert researcher.navigation_trace == [
        {
            "tool": "web_fetch",
            "status": "error",
            "url": "https://example.com/app",
            "title": "Example App",
            "retrieval": "fetch",
            "error": "fetch blocked",
        },
        {
            "tool": "browser_read",
            "status": "error",
            "url": "https://example.com/app",
            "title": "Example App",
            "retrieval": "browser",
            "error": "bot check",
        },
    ]
    summary = researcher._navigation_trace_summary()
    assert "browser_read https://example.com/app -> error; bot check" in summary


def test_fetch_and_extract_uses_private_browser_when_text_extraction_is_weak(monkeypatch):
    monkeypatch.setattr("src.settings.get_setting", lambda key, default=None: True)

    nav = _WeakFetchNavigator()
    researcher = _researcher_for_fetch(nav)
    calls = []

    async def _fake_llm(messages, **kwargs):
        calls.append(messages[1]["content"])
        if "Cookie banner" in messages[1]["content"]:
            return json.dumps({
                "rational": "boilerplate",
                "summary": "No relevant information found.",
                "evidence": "",
            })
        return json.dumps({
            "rational": "browser fallback",
            "summary": "Useful browser-rendered evidence.",
            "evidence": "Browser-rendered evidence about the actual question.",
        })

    researcher._llm = _fake_llm

    finding = asyncio.run(researcher._fetch_and_extract(
        "https://example.com/app",
        "What does the app do?",
        "Example App",
    ))

    assert nav.fetch_called is True
    assert nav.browser_called is True
    assert len(calls) == 2
    assert finding["retrieval"] == "browser"
    assert finding["summary"] == "Useful browser-rendered evidence."
    assert researcher.analyzed_urls[0]["retrieval"] == "browser"
    assert researcher.navigation_trace[0]["tool"] == "web_fetch"
    assert researcher.navigation_trace[0]["status"] == "low_quality"
    assert researcher.navigation_trace[-1]["tool"] == "browser_read"
    assert researcher.navigation_trace[-1]["status"] == "ok"
