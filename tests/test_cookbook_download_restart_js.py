from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNING_JS = ROOT / "static" / "js" / "cookbookRunning.js"


def test_retry_download_tombstones_superseded_session():
    source = RUNNING_JS.read_text(encoding="utf-8")
    assert "_tombstoneTask(oldSessionId)" in source
    assert "Restart looked like it added a second card" in source


def test_inactive_download_remove_skips_stop_session():
    source = RUNNING_JS.read_text(encoding="utf-8")
    idx = source.index("Inactive download rows are UI history only")
    block = source[idx:idx + 500]
    assert "task.type === 'download'" in block
    assert "_animateOutThenRemove(el, task.sessionId)" in block
    assert "_stopCookbookSession(task)" not in block


def test_auto_retry_download_reuses_same_card():
    source = RUNNING_JS.read_text(encoding="utf-8")
    assert "_retryDownload(_nm, _p, _retrySid)" in source
    # Auto-retry must not drop the row before relaunching — that left duplicates.
    auto_idx = source.index("_retryDownload(_nm, _p, _retrySid)")
    auto_block = source[auto_idx - 400:auto_idx + 80]
    assert "_removeTask(task.sessionId)" not in auto_block
