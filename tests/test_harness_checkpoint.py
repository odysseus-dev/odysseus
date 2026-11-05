"""Files are copied before a rewrite and can be restored."""
from src import harness_checkpoint


def test_snapshot_and_rewind_restores_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(harness_checkpoint, "AGENT_WORKSPACE_DIR", str(tmp_path))
    target = tmp_path / "note.txt"
    target.write_text("before", encoding="utf-8")
    ctx = {"owner": "ada", "session_id": "s1", "request_id": "t1"}
    assert harness_checkpoint.snapshot_existing(str(target), ctx)
    target.write_text("after", encoding="utf-8")
    result = harness_checkpoint.rewind_latest("ada", "s1")
    assert result["ok"] is True
    assert target.read_text(encoding="utf-8") == "before"
