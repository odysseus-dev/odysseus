import asyncio
import json
import sys
import types

from src.research_navigator import ResearchNavigator, assess_source, parse_research_actions


def test_parse_research_actions_accepts_strict_actions_array():
    actions = parse_research_actions(json.dumps({
        "actions": [
            {"tool": "web_search", "query": "official docs"},
            {"tool": "web_fetch", "args": {"url": "https://example.com"}},
            {"tool": "browser_snapshot", "url": "https://example.com/rendered"},
            {"tool": "private_browser", "action": "snapshot", "url": "https://example.com/app"},
            {"tool": "bash", "cmd": "curl example.com"},
        ]
    }))

    assert [a.tool for a in actions] == [
        "web_search",
        "web_fetch",
        "browser_snapshot",
        "private_browser",
    ]
    assert actions[0].args["query"] == "official docs"
    assert actions[1].args == {"url": "https://example.com"}
    assert actions[2].args == {"url": "https://example.com/rendered"}
    assert actions[3].args == {"action": "snapshot", "url": "https://example.com/app"}


def test_research_navigator_search_records_provider(monkeypatch):
    providers_mod = types.ModuleType("src.search.providers")
    providers_mod._get_search_settings = lambda: {"search_provider": "searxng"}
    core_mod = types.ModuleType("src.search.core")
    core_mod._build_provider_chain = lambda provider: ["searxng", "duckduckgo"]
    core_mod._call_provider = lambda provider, query, count: (
        [{"url": "https://example.com", "title": "Example"}] if provider == "duckduckgo" else []
    )
    monkeypatch.setitem(sys.modules, "src.search.providers", providers_mod)
    monkeypatch.setitem(sys.modules, "src.search.core", core_mod)

    nav = ResearchNavigator()
    results = asyncio.run(nav.search("example", count=5))

    assert results == [{"url": "https://example.com", "title": "Example"}]
    assert nav.providers_used == ["duckduckgo"]


def test_research_navigator_fetch_normalizes_content(monkeypatch):
    monkeypatch.setattr(
        "src.search.content.fetch_webpage_content",
        lambda url, **kwargs: {
            "success": True,
            "title": "Example Domain",
            "content": "Readable content",
            "og_image": "https://example.com/og.png",
        },
    )

    page = asyncio.run(ResearchNavigator().fetch("https://example.com"))

    assert page.success is True
    assert page.title == "Example Domain"
    assert page.content == "Readable content"
    assert page.retrieval == "fetch"


def test_assess_source_identifies_primary_and_commercial_sources():
    official = assess_source("https://docs.example.com/release-notes", summary="Useful docs")
    commercial = assess_source("https://example-review-site.com/best-laptops", summary="Useful list")

    assert official.kind == "primary"
    assert official.score > commercial.score
    assert commercial.kind == "commercial"
