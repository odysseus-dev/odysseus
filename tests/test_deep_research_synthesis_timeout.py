"""Regression tests for #6986: configurable Deep Research synthesis timeout
and single-attempt budget protection against 3x retry hangs on slow local models."""

import asyncio
from unittest.mock import AsyncMock, patch

from src.deep_research import DeepResearcher
from src.research_handler import ResearchHandler
from src.settings import DEFAULT_SETTINGS


def test_default_synthesis_timeout_setting_exists():
    assert DEFAULT_SETTINGS["research_synthesis_timeout_seconds"] == 600


def test_synthesis_and_final_report_use_synthesis_timeout_and_single_retry():
    recorded_calls = []

    async def fake_llm_call_async(**kwargs):
        recorded_calls.append(kwargs)
        # Return >= 400 words so _final_report does not trigger expansion
        return " ".join(["finding"] * 450)

    researcher = DeepResearcher(
        llm_endpoint="http://localhost:11434/v1",
        llm_model="qwen3:30b",
        synthesis_timeout=720,
    )
    assert researcher.synthesis_timeout == 720

    async def run():
        with patch("src.llm_core.llm_call_async", side_effect=fake_llm_call_async):
            await researcher._synthesize(
                question="Test question",
                findings=[{"url": "https://example.com", "title": "T", "summary": "S"}],
                current_report="",
            )
            await researcher._final_report(
                question="Test question",
                report=" ".join(["draft"] * 450),
            )

    asyncio.run(run())
    assert len(recorded_calls) == 2
    for call in recorded_calls:
        assert call["timeout"] == 720
        assert call["max_retries"] == 1


def test_final_report_expansion_also_uses_synthesis_timeout_and_single_retry():
    recorded_calls = []

    async def fake_llm_call_async(**kwargs):
        recorded_calls.append(kwargs)
        if len(recorded_calls) == 1:
            return "Short draft."
        return " ".join(["expanded"] * 450)

    researcher = DeepResearcher(
        llm_endpoint="http://localhost:11434/v1",
        llm_model="qwen3:30b",
        synthesis_timeout=540,
    )

    async def run():
        with patch("src.llm_core.llm_call_async", side_effect=fake_llm_call_async):
            out = await researcher._final_report(
                question="Test question",
                report="Initial findings.",
            )
            assert len(out.split()) == 450

    asyncio.run(run())
    assert len(recorded_calls) == 2
    assert all(c["timeout"] == 540 and c["max_retries"] == 1 for c in recorded_calls)


def test_research_handler_forwards_synthesis_timeout_setting():
    handler = ResearchHandler()
    captured = {}

    class DummyResearcher:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.analyzed_urls = []
            self.findings = []

        async def research(self, *args, **kwargs):
            return "done"

        def get_stats(self):
            return {}

    async def run():
        with (
            patch.object(handler, "_probe_endpoint", new=AsyncMock()),
            patch("src.deep_research.DeepResearcher", DummyResearcher),
            patch(
                "src.settings.get_setting",
                side_effect=lambda k, d=None: 900 if k == "research_synthesis_timeout_seconds" else d,
            ),
        ):
            await handler.call_research_service(
                query="Explain X",
                llm_endpoint="http://localhost:11434/v1",
                llm_model="qwen3:30b",
            )

    asyncio.run(run())
    assert captured["synthesis_timeout"] == 900
