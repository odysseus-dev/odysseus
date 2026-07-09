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


def test_retry_download_skips_tombstone_when_session_reused():
    source = RUNNING_JS.read_text(encoding="utf-8")
    idx = source.index("async function _retryDownload")
    block = source[idx:idx + 2800]
    assert "if (newSessionId === oldSessionId)" in block
    reuse_idx = block.index("if (newSessionId === oldSessionId)")
    tombstone_idx = block.index("_tombstoneTask(oldSessionId)")
    assert reuse_idx < tombstone_idx


def test_local_task_platform_used_for_windows_log_poll():
    source = (ROOT / "static" / "js" / "cookbook.js").read_text(encoding="utf-8")
    idx = source.index("export function _getPlatform")
    block = source[idx:idx + 520]
    assert "hostOrTask.platform || hostOrTask.payload?.platform" in block


def test_local_windows_download_uses_detached_log_path():
    source = RUNNING_JS.read_text(encoding="utf-8")
    idx = source.index("export function _tmuxCmd")
    block = source[idx:idx + 500]
    assert "const localWin = !_taskRemoteHost(task)" in block
    assert "navigator.userAgent" not in block
    assert "Get-Content (Join-Path $env:TEMP 'odysseus-tmux" in source


def test_tmux_cmd_uses_server_platform_not_browser_os():
    source = RUNNING_JS.read_text(encoding="utf-8")
    idx = source.index("export function _tmuxCmd")
    block = source[idx:idx + 400]
    assert "_isWindows(task) || _isWindows('local')" in block
    assert "navigator" not in block


def test_download_stopped_is_terminal_for_active_output():
    source = RUNNING_JS.read_text(encoding="utf-8")
    idx = source.index("function _downloadOutputLooksActive")
    block = source[idx:idx + 450]
    assert "DOWNLOAD_STOPPED" in block


def test_failed_stop_rolls_back_live_serve_statuses():
    source = RUNNING_JS.read_text(encoding="utf-8")
    idx = source.index("async function _onTaskStop")
    block = source[idx:idx + 700]
    assert "_RECONNECT_STATUSES.includes(priorStatus)" in block
    assert "el.dataset.status = priorStatus" in block


def test_running_tab_reconnect_survives_rerender():
    source = RUNNING_JS.read_text(encoding="utf-8")
    assert "function _ensureTaskReconnect(el, task)" in source
    assert "function _activateRunningTab()" in source
    assert "_activateRunningTab();" in source
    idx = source.index("if (_isRunningTabVisible()) {")
    block = source[idx:idx + 320]
    assert "_RECONNECT_STATUSES.includes(task.status)" in block
    assert "_ensureTaskReconnect(el, task)" in block


def test_stale_progress_restart_uses_retry_download_bookkeeping():
    source = RUNNING_JS.read_text(encoding="utf-8")
    idx = source.index("badge.textContent = _startupStalled ? '0% stall — retrying'")
    block = source[idx:idx + 1400]
    assert "_retryDownload(task.name || task.repo, dlPayload, task.sessionId)" in block
    assert "fetch('/api/model/download'" not in block


def test_auto_retry_download_reuses_same_card():
    source = RUNNING_JS.read_text(encoding="utf-8")
    assert "_retryDownload(_nm, _p, _retrySid)" in source
    # Auto-retry must not drop the row before relaunching — that left duplicates.
    auto_idx = source.index("_retryDownload(_nm, _p, _retrySid)")
    auto_block = source[auto_idx - 400:auto_idx + 80]
    assert "_removeTask(task.sessionId)" not in auto_block


def test_restart_clears_user_stopped_flag():
    source = RUNNING_JS.read_text(encoding="utf-8")
    assert "status: 'running',\n        _userStopped: false," in source
    retry_dl_idx = source.index("const updated = {", source.index("async function _retryDownload"))
    retry_dl_block = source[retry_dl_idx:retry_dl_idx + 400]
    assert "_userStopped: false" in retry_dl_block


def test_download_progress_persisted_for_rerender():
    source = RUNNING_JS.read_text(encoding="utf-8")
    assert "Persist so _renderRunningTab (state sync / background poll) keeps" in source
    assert "_updateTask(task.sessionId, { progress: progressText })" in source
    assert "_applyTaskBadge(badge, task, progressText)" in source
    assert "function _applyTaskBadge" in source


def test_task_menu_reloads_live_status_before_stop_actions():
    source = RUNNING_JS.read_text(encoding="utf-8")
    idx = source.index("menuBtn.addEventListener('click'")
    # Window must cover the full Run-section items (queued Start now, Reconnect,
    # Stop) — the handler grew past 1600 chars when queued downloads were added.
    block = source[idx:idx + 2200]
    assert "_loadTasks().find(t => t.sessionId === task.sessionId)" in block
    assert "if (menuStatus === 'running')" in block
    assert "hide Stop after the user stopped" in block
