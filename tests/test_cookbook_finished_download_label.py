from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "static/js/cookbookRunning.js"


def test_completed_downloads_use_the_finished_ui_label():
    source = SOURCE.read_text(encoding="utf-8")

    assert "status === 'done' || status === 'completed'" in source
    assert "if (_isFinishedDownload(status, type)) return 'finished';" in source
    assert "_isFinishedDownload(task.status, task.type)" in source
