import asyncio
import importlib.util
from pathlib import Path


_HANDLER_PATH = (
    Path(__file__).resolve().parents[1] / "services" / "research" / "research_handler.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "_services_research_handler_under_test",
    _HANDLER_PATH,
)
research_handler = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(research_handler)
ResearchHandler = research_handler.ResearchHandler


class _LegacyEngine:
    findings = ["one"]
    source_reports = ["source"]

    def __init__(self):
        self.progress_tracker = type(
            "ProgressTracker",
            (),
            {"counters": {"searches_executed": 1, "urls_processed": 1}},
        )()

    def start_research(self, query, max_time):
        return f"legacy result for {query} in {max_time}s"


def test_services_research_fallback_uses_running_loop(monkeypatch):
    handler = ResearchHandler.__new__(ResearchHandler)
    handler._legacy_engine = _LegacyEngine()
    handler._active_tasks = {}

    def fail_get_event_loop():
        raise AssertionError("fallback should use the active running loop")

    monkeypatch.setattr(research_handler.asyncio, "get_event_loop", fail_get_event_loop)

    async def run_fallback():
        return await handler._fallback_research(
            "topic",
            "http://llm.example",
            "test-model",
            30,
            "primary failed",
        )

    result = asyncio.run(run_fallback())

    assert "legacy result for topic in 30s" in result
