"""Oversized tool output is kept on disk and the model gets the path."""
from pathlib import Path

from src import tool_spill


def test_spill_writes_the_full_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_spill, "AGENT_WORKSPACE_DIR", str(tmp_path))
    shown, path = tool_spill.spill_tool_output("x" * 2000, tool="bash", session_id="s", limit=400)
    assert path
    assert Path(path).read_text(encoding="utf-8") == "x" * 2000
    assert path in shown
    assert len(shown) <= 400
