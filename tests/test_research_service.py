"""Tests for ResearchService — correct handling of the handler's string report.

ResearchHandler.call_research_service returns a *formatted markdown string*,
not a dict. ResearchService.research() must consume that contract without
raising (the previous code called ``.get()`` on the string and blew up on
every successful research call).
"""

import asyncio

import pytest

from services.research.service import (
    ResearchService,
    ResearchResult,
    ResearchSource,
)


# A faithful slice of what ResearchHandler._format_research_report emits.
SAMPLE_REPORT = """---

## Research Summary

**Duration:** 12.3s | **Rounds:** 3 | **Queries:** 5 | **URLs Analyzed:** 7

---

# Findings

Quantum error correction saw major advances in 2024. See [an inline note](https://inline.example/not-a-source) here.

### Sources

- [Surface Codes Paper](https://example.com/surface-codes)
- [Lab Announcement](https://example.com/lab)
- [Surface Codes Paper](https://example.com/surface-codes)

---

**The AI has analyzed all research findings above.**
"""


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _StubHandler:
    """Stands in for ResearchHandler; returns a string like the real one."""

    def __init__(self, report):
        self._report = report
        self.called_with = None

    async def call_research_service(self, topic, llm_endpoint, llm_model,
                                    max_time=300, progress_callback=None):
        self.called_with = (topic, llm_endpoint, llm_model, max_time)
        return self._report


class TestResearchOnStringReport:
    def _service(self, report):
        svc = ResearchService()
        svc.handler = _StubHandler(report)
        return svc

    def test_does_not_raise_on_string_report(self):
        svc = self._service(SAMPLE_REPORT)
        result = _run(svc.research("quantum", "http://llm", "model"))
        assert isinstance(result, ResearchResult)

    def test_summary_is_the_report(self):
        svc = self._service(SAMPLE_REPORT)
        result = _run(svc.research("quantum", "http://llm", "model"))
        assert "Quantum error correction" in result.summary
        assert result.query == "quantum"

    def test_sources_parsed_and_deduped(self):
        svc = self._service(SAMPLE_REPORT)
        result = _run(svc.research("quantum", "http://llm", "model"))
        urls = [s.url for s in result.sources]
        assert urls == [
            "https://example.com/surface-codes",
            "https://example.com/lab",
        ]
        assert all(isinstance(s, ResearchSource) for s in result.sources)

    def test_inline_links_outside_sources_section_ignored(self):
        svc = self._service(SAMPLE_REPORT)
        result = _run(svc.research("quantum", "http://llm", "model"))
        urls = [s.url for s in result.sources]
        assert "https://inline.example/not-a-source" not in urls

    def test_duration_recorded(self):
        svc = self._service(SAMPLE_REPORT)
        result = _run(svc.research("quantum", "http://llm", "model"))
        assert result.duration_seconds >= 0.0

    def test_empty_report_yields_no_sources(self):
        svc = self._service("")
        result = _run(svc.research("quantum", "http://llm", "model"))
        assert result.sources == []
        assert result.summary == ""


class TestParseSources:
    def test_returns_empty_for_empty_input(self):
        assert ResearchService._parse_sources("") == []

    def test_handles_titleless_link(self):
        report = "### Sources\n\n- [](https://example.com/x)\n"
        sources = ResearchService._parse_sources(report)
        assert len(sources) == 1
        assert sources[0].url == "https://example.com/x"
        assert sources[0].title == ""

    def test_section_ends_at_next_heading(self):
        report = (
            "### Sources\n\n"
            "- [A](https://a.example)\n\n"
            "### Notes\n\n"
            "- [B](https://b.example)\n"
        )
        urls = [s.url for s in ResearchService._parse_sources(report)]
        assert urls == ["https://a.example"]


class TestDictBackCompat:
    """A handler that returns a dict (legacy shape) must still work."""

    def test_dict_result_still_parsed(self):
        svc = ResearchService()

        class _DictHandler:
            async def call_research_service(self, *a, **k):
                return {
                    "summary": "done",
                    "sources": [
                        {"url": "https://x.example", "title": "X",
                         "snippet": "s", "relevance": 0.9},
                        "bad source row",
                    ],
                    "sections": ["intro"],
                    "tokens_used": 42,
                }

        svc.handler = _DictHandler()
        result = _run(svc.research("q", "http://llm", "model"))
        assert result.summary == "done"
        assert result.tokens_used == 42
        assert result.sections == ["intro"]
        assert result.sources[0].url == "https://x.example"
        assert result.sources[0].relevance == 0.9
