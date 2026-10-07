import json

from src import research_handler
from src.research_handler import ResearchHandler


def test_research_sources_preserve_quality_metadata():
    sources = ResearchHandler._extract_sources([
        {
            "url": "https://docs.example.com/release-notes",
            "title": "Release Notes",
            "summary": "Detailed useful evidence.",
            "retrieval": "browser",
            "source_kind": "primary",
            "source_score": 87,
            "source_reason": "primary project/vendor source; browser-read",
        }
    ])

    assert sources == [{
        "url": "https://docs.example.com/release-notes",
        "title": "Release Notes",
        "retrieval": "browser",
        "source_kind": "primary",
        "source_score": 87,
        "source_reason": "primary project/vendor source; browser-read",
    }]


def test_research_raw_findings_preserve_quality_metadata():
    findings = ResearchHandler._extract_raw_findings([
        {
            "url": "https://example.com/report",
            "title": "Report",
            "summary": "Detailed useful evidence.",
            "retrieval": "fetch",
            "source_kind": "secondary",
            "source_score": "55",
        }
    ])

    assert findings == [{
        "url": "https://example.com/report",
        "title": "Report",
        "summary": "Detailed useful evidence.",
        "retrieval": "fetch",
        "source_kind": "secondary",
        "source_score": 55,
    }]


def test_research_handler_returns_active_navigation_trace():
    handler = ResearchHandler()
    session_id = "trace-session"

    class _Researcher:
        navigation_trace = [{"tool": "web_search", "query": "docs", "status": "ok", "results": 3}]

    handler._active_tasks[session_id] = {"researcher": _Researcher()}

    assert handler.get_navigation_trace(session_id) == [
        {"tool": "web_search", "query": "docs", "status": "ok", "results": 3}
    ]


def test_research_handler_returns_active_action_trace():
    handler = ResearchHandler()
    session_id = "action-trace-session"

    class _Researcher:
        action_trace = [{
            "round": 1,
            "source": "planner",
            "tool": "web_search",
            "query": "official docs",
        }]

    handler._active_tasks[session_id] = {"researcher": _Researcher()}

    assert handler.get_action_trace(session_id) == [
        {"round": 1, "source": "planner", "tool": "web_search", "query": "official docs"}
    ]


def test_research_handler_returns_active_source_coverage():
    handler = ResearchHandler()
    session_id = "coverage-session"

    class _Researcher:
        def _source_coverage(self):
            return {
                "sources_analyzed": 2,
                "useful_findings": 1,
                "source_mix": {"primary": 1},
            }

    handler._active_tasks[session_id] = {"researcher": _Researcher()}

    assert handler.get_source_coverage(session_id) == {
        "sources_analyzed": 2,
        "useful_findings": 1,
        "source_mix": {"primary": 1},
    }


def test_research_handler_persists_action_trace(tmp_path, monkeypatch):
    data_dir = tmp_path / "deep_research"
    data_dir.mkdir()
    monkeypatch.setattr(research_handler, "RESEARCH_DATA_DIR", data_dir)
    handler = ResearchHandler.__new__(ResearchHandler)
    handler._active_tasks = {}

    class _Researcher:
        findings = []
        analyzed_urls = []
        navigation_trace = []
        action_trace = [{
            "round": 1,
            "source": "planner",
            "tool": "web_search",
            "query": "official docs",
        }]

        def _source_state_summary(self):
            return "No sources gathered yet."

        def _source_coverage(self):
            return {"sources_analyzed": 0, "useful_findings": 0}

    handler._save_result("action-trace-save", {
        "query": "q",
        "status": "done",
        "result": "r",
        "started_at": 1,
        "researcher": _Researcher(),
    })

    data = json.loads((data_dir / "action-trace-save.json").read_text(encoding="utf-8"))
    assert data["action_trace"] == [{
        "round": 1,
        "source": "planner",
        "tool": "web_search",
        "query": "official docs",
    }]
    assert handler.get_action_trace("action-trace-save") == data["action_trace"]


def test_visual_report_diagnostics_include_action_and_navigation_trace():
    data = {
        "action_trace": [{
            "round": 1,
            "source": "planner",
            "tool": "web_search",
            "query": "official docs",
        }, {
            "round": 1,
            "source": "planner",
            "tool": "browser_read",
            "url": "https://example.com/rendered",
            "requested_by": "private_browser.snapshot",
        }, {
            "round": 1,
            "source": "planner",
            "status": "skipped",
            "tool": "web_search",
            "query": "can you search",
            "reason": "meta search request",
        }],
        "navigation_trace": [{
            "tool": "web_search",
            "query": "official docs",
            "status": "ok",
            "results": 3,
        }],
        "source_state": "Sources analyzed: 1; useful findings: 1.",
    }

    md = ResearchHandler._research_diagnostics_markdown(data)

    assert "<summary>Research trace</summary>" in md
    assert "### Planned Actions" in md
    assert "`planner` -> `web_search` official docs" in md
    assert "`planner` -> `browser_read` via `private_browser.snapshot` https://example.com/rendered" in md
    assert "`planner` skipped `web_search` can you search — meta search request" in md
    assert "### Navigation" in md
    assert "`web_search` official docs -> **ok** (3 results)" in md
    assert "### Source State" in md
