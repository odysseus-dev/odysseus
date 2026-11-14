"""DR-2 unit tests for timeout alignment, thinking budget bounding, and rp-*.json persistence."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from src.deep_research import DeepResearcher
from src.research_handler import ResearchHandler
from src.tools.research import do_trigger_research


@pytest.mark.asyncio
async def test_trigger_research_chat_budget_accommodates_synthesis(monkeypatch):
    captured = {}

    class _DummyResp:
        status_code = 200
        text = "ok"

        def json(self):
            return {"session_id": "rp-dr2test123"}

    class _DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None, headers=None):
            captured["payload"] = json
            return _DummyResp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _DummyClient)

    res = await do_trigger_research(
        '{"topic": "Compare NVMe drives"}',
        owner="admin",
        chat_session_id="chat-abc123",
    )
    assert res["exit_code"] == 0
    assert captured["payload"]["max_rounds"] == 2
    assert captured["payload"]["max_time"] >= 180


def test_step_timeout_bounds_to_remaining_wall_clock_budget():
    r = DeepResearcher(
        llm_endpoint="http://127.0.0.1:11434/v1",
        llm_model="deepseek-r1:32b",
        max_time=120,
    )
    r._start_time = time.time() - 75.0  # 45s left
    bounded = r._step_timeout(180, reserve_seconds=15)
    assert 15 <= bounded <= 35


def test_save_result_persists_report_model_and_rounds_top_level(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("src.research_handler.RESEARCH_DATA_DIR", tmp_path)
    handler = ResearchHandler()

    class _StubResearcher:
        findings = []
        analyzed_urls = ["https://example.com"]
        navigation_trace = []
        action_trace = []

        def _source_state_summary(self):
            return "1 source"

        def _source_coverage(self):
            return {"total": 1}

    entry = {
        "query": "DR-2 persistence test",
        "status": "done",
        "result": "## Summary\nFormatted report body",
        "raw_report": "Raw report body",
        "stats": {"Model": "gemma4:31b", "Rounds": 2, "Queries": 4},
        "category": "comparison",
        "started_at": 1700000000.0,
        "owner": "admin",
        "researcher": _StubResearcher(),
    }
    handler._save_result("rp-dr2persist01", entry)

    saved = json.loads((tmp_path / "rp-dr2persist01.json").read_text(encoding="utf-8"))
    assert saved["report"] == "Raw report body"
    assert saved["result"] == "## Summary\nFormatted report body"
    assert saved["model"] == "gemma4:31b"
    assert saved["rounds"] == 2
    assert saved["rounds_completed"] == 2
