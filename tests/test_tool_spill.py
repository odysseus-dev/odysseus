"""Oversized tool output is kept on disk and the model gets the path."""
from pathlib import Path

from src import tool_spill


def _spill(tmp_path, monkeypatch, text, **kwargs):
    monkeypatch.setattr(tool_spill, "AGENT_WORKSPACE_DIR", str(tmp_path))
    return tool_spill.spill_tool_output(text, **kwargs)


def test_spill_writes_the_full_capture(tmp_path, monkeypatch):
    text = "x" * 2000
    shown, path = _spill(tmp_path, monkeypatch, text, tool="bash", session_id="s", limit=400)
    assert path
    assert Path(path).read_text(encoding="utf-8") == text
    assert path in shown
    assert len(shown) <= 400


def test_small_capture_is_returned_untouched(tmp_path, monkeypatch):
    shown, path = _spill(tmp_path, monkeypatch, "short", tool="bash", limit=400)
    assert shown == "short"
    assert path is None
    assert not (tmp_path / "spills").exists()


def test_none_capture_is_empty(tmp_path, monkeypatch):
    shown, path = _spill(tmp_path, monkeypatch, None, tool="bash", limit=400)
    assert shown == ""
    assert path is None


def test_head_is_kept_and_tail_lives_on_disk(tmp_path, monkeypatch):
    text = "HEAD" + ("m" * 5000) + "TAIL"
    shown, path = _spill(tmp_path, monkeypatch, text, tool="bash", limit=400)
    assert shown.startswith("HEAD")
    assert "full output in" in shown
    assert Path(path).read_text(encoding="utf-8").endswith("TAIL")


def test_file_names_cannot_escape_the_spill_folder(tmp_path, monkeypatch):
    shown, path = _spill(
        tmp_path,
        monkeypatch,
        "z" * 2000,
        tool="../../etc/passwd",
        session_id="../..",
        limit=400,
    )
    spill_path = Path(path)
    assert spill_path.parent == tmp_path / "spills"
    assert "/etc/passwd" not in spill_path.name
    assert ".." not in spill_path.name


def test_note_is_truncated_when_the_limit_is_tiny(tmp_path, monkeypatch):
    shown, path = _spill(tmp_path, monkeypatch, "q" * 500, tool="bash", limit=10)
    assert len(shown) <= 10
    assert Path(path).exists()
