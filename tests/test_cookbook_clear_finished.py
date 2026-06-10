from pathlib import Path


SOURCE = Path("static/js/cookbookRunning.js")


def test_clear_finished_keeps_queued_downloads():
    source = SOURCE.read_text(encoding="utf-8")
    clear_block = source[
        source.index("// Wire clear all buttons") : source.index("// Wire \"Stop all\" buttons")
    ]

    assert "function _isCookbookTaskClearable(task)" in source
    assert "!['running', 'queued', 'ready'].includes(task.status)" in source
    assert "t.status !== 'running'" not in clear_block


def test_clear_finished_uses_same_filter_for_removed_and_remaining_tasks():
    source = SOURCE.read_text(encoding="utf-8")

    assert (
        "const toRemove = allTasks.filter(t => "
        "_sameCookbookHost(t, host) && _isCookbookTaskClearable(t));"
    ) in source
    assert (
        "const remaining = allTasks.filter(t => "
        "!_sameCookbookHost(t, host) || !_isCookbookTaskClearable(t));"
    ) in source
