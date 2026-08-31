from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNING_JS = ROOT / "static" / "js" / "cookbookRunning.js"


def test_inactive_download_remove_skips_stop_session():
    source = RUNNING_JS.read_text(encoding="utf-8")
    idx = source.index("Inactive download rows are UI history only")
    block = source[idx:idx + 500]
    assert "liveTask.type === 'download'" in block
    assert "_animateOutThenRemove(el, liveTask.sessionId)" in block
    assert "_stopCookbookSession(task)" not in block


def test_menu_kill_handler_uses_refreshed_task_not_stale_closure():
    source = RUNNING_JS.read_text(encoding="utf-8")
    idx = source.index(".cookbook-task-action-kill').addEventListener('click'")
    block = source[idx:idx + 900]
    assert "const liveTask = _loadTasks().find(t => t.sessionId === task.sessionId)" in block
    assert "const liveStatus = liveTask.status || el.dataset.status" in block
    assert "await _onTaskStop(el, liveTask" in block


def test_download_stopped_is_terminal_for_active_output():
    source = RUNNING_JS.read_text(encoding="utf-8")
    idx = source.index("function _downloadOutputLooksActive")
    block = source[idx:idx + 450]
    assert "DOWNLOAD_STOPPED" in block


def test_failed_stop_rolls_back_live_serve_and_download_statuses():
    source = RUNNING_JS.read_text(encoding="utf-8")
    idx = source.index("async function _onTaskStop")
    block = source[idx:idx + 1000]
    assert "_RECONNECT_STATUSES.includes(priorStatus)" in block
    assert "task.type === 'download' && priorStatus === 'running'" in block
    failed = block[block.index("if (!stopOk)"):block.index("if (removeAfter)")]
    assert "el.dataset.status = priorStatus" in block
    assert "_userStopped: false, status: priorStatus" in failed
    assert "_applyStoppedTaskCard" not in failed


def test_stop_cookbook_session_sends_repo_id_only_for_downloads():
    source = RUNNING_JS.read_text(encoding="utf-8")
    idx = source.index("async function _stopCookbookSession")
    block = source[idx:idx + 1300]
    assert "task_type: taskType" in block
    assert "if (taskType === 'download')" in block
    assert "body.repo_id = repoId" in block
    assert "body.local_dir = localDir" in block
    assert "body.include = include" in block
    assert block.index("if (taskType === 'download')") < block.index("body.repo_id = repoId")


def test_serve_cleanup_happens_only_after_stop_succeeds():
    source = RUNNING_JS.read_text(encoding="utf-8")
    idx = source.index("async function _executeTaskStop")
    block = source[idx:source.index("async function _onTaskStop", idx)]
    stop_idx = block.index("const result = await _stopCookbookSession(task)")
    success_idx = block.index("if (!(result && result.ok)) return false")
    endpoint_idx = block.index("await _removeEndpointByUrl")
    unload_idx = block.index("body: JSON.stringify({ command: ollamaUnload })")
    assert stop_idx < success_idx < endpoint_idx
    assert stop_idx < success_idx < unload_idx
