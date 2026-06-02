"""Regression tests for issue #1363 — after a deep-research job finishes, asking
the agent to "check it out / read that report" had it web_fetch the HTML report
render (and drift into unrelated searches) instead of reading the saved report.

Per the maintainer's diagnosis the fix is in the agent/tool-routing path: a
finished report should be read via `manage_research` (action read), resolving the
most-recent id with `action list` when none is given — not by fetching the
`/api/research/report/{id}` HTML.

These tests pin both halves:
1. the read path the agent is told to use actually returns the report text for a
   saved `rp-...` id, and
2. the agent instructions steer to `manage_research read` and away from
   web_fetching the HTML report.
"""
import json
from pathlib import Path

import pytest

from src.tool_implementations import do_manage_research
from src.agent_loop import TOOL_SECTIONS

_DATA_DIR = Path("data/deep_research")


@pytest.fixture
def saved_report():
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    rid = "rp-testreport1363"
    path = _DATA_DIR / f"{rid}.json"
    path.write_text(json.dumps({
        "query": "trending blender video ideas",
        "result": "## Findings\nShort-form Geometry Nodes tutorials are trending.",
        "sources": [{"title": "Example", "url": "https://example.com"}],
        "completed_at": 123,
    }), encoding="utf-8")
    try:
        yield rid
    finally:
        path.unlink(missing_ok=True)


async def test_manage_research_read_returns_report_text(saved_report):
    res = await do_manage_research(json.dumps({"action": "read", "id": saved_report}))
    out = res.get("output", "")
    # The agent must get the actual report body (not HTML, not an error).
    assert "Geometry Nodes tutorials are trending" in out
    assert "trending blender video ideas" in out
    assert res.get("exit_code") == 0


async def test_panel_launched_rp_id_is_valid_for_read(saved_report):
    # rp-* ids (panel-launched research) contain a hyphen; the read path's id
    # guard must accept them, not reject them as invalid.
    res = await do_manage_research(json.dumps({"action": "read", "id": saved_report}))
    assert "error" not in res, res


def test_instructions_route_report_reads_to_manage_research():
    desc = TOOL_SECTIONS["manage_research"]
    # Steers to the read tool for a finished report...
    assert "read that report" in desc.lower() or "that report" in desc.lower()
    assert "action:list" in desc or "action: list" in desc
    # ...and explicitly away from fetching the HTML report endpoint.
    assert "/api/research/report/" in desc
    assert "web_fetch" in desc.lower() or "app_api" in desc.lower()
