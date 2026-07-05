import shlex
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNING_JS = ROOT / "static" / "js" / "cookbookRunning.js"


def _between(source, start, end):
    start_idx = source.index(start)
    end_idx = source.index(end, start_idx)
    return source[start_idx:end_idx]


def test_windows_graceful_kill_reuses_recursive_stop_tree_helper():
    source = RUNNING_JS.read_text(encoding="utf-8")
    wrapper = _between(source, "function _winPowerShellCmd(task, ps)", "function _winSessionStopTreePs(task)")
    helper = _between(source, "function _winSessionStopTreePs(task)", "function _tmuxGracefulKill(task)")
    graceful = _between(source, "function _tmuxGracefulKill(task)", "function _shQuote(value)")
    win_session = _between(source, "function _winSessionCmd(task, tmuxArgs)", "function _winPowerShellCmd(task, ps)")

    assert "function Stop-Tree([int]$Id)" in helper
    assert "('ParentProcessId = ' + $Id)" in helper
    assert "Stop-Tree ([int]$p)" in helper
    assert "${_shQuote(command)}" in wrapper
    assert "_winSessionStopTreePs(task)" in win_session
    assert "_winPowerShellCmd(task, ps)" in win_session
    assert "_winSessionStopTreePs(task)" in graceful
    assert "_winPowerShellCmd(task, ps)" in graceful
    assert "Stop-Process -Id $p -Force" not in graceful
    assert '-Filter "ParentProcessId = $Id"' not in helper
    assert 'powershell -Command \\\\"${ps}\\\\"' not in source


def _posix_quote(value):
    return "'" + value.replace("'", "'\\''") + "'"


def test_remote_windows_stop_tree_payload_survives_shell_parsing():
    ps = (
        "function Stop-Tree([int]$Id) { "
        "Get-CimInstance Win32_Process -Filter ('ParentProcessId = ' + $Id) "
        "-ErrorAction SilentlyContinue | ForEach-Object { Stop-Tree ([int]$_.ProcessId) }; "
        "Stop-Process -Id $Id -Force -ErrorAction SilentlyContinue }; "
        "$p = Get-Content '$env:TEMP\\odysseus-sessions\\serve_abc.pid' "
        "-ErrorAction SilentlyContinue; "
        "if ($p -match '^\\d+$') { Stop-Tree ([int]$p) }"
    )
    remote_command = f'powershell -Command "{ps}"'
    shell_command = f"ssh -p 2222 winbox {_posix_quote(remote_command)}"

    argv = shlex.split(shell_command)

    assert argv == ["ssh", "-p", "2222", "winbox", remote_command]
    assert "$Id" in argv[-1]
    assert "$_.ProcessId" in argv[-1]
    assert "$env:TEMP" in argv[-1]
    assert "$p" in argv[-1]


def test_retry_replacement_tombstones_only_superseded_download_sessions():
    source = RUNNING_JS.read_text(encoding="utf-8")
    retry = _between(source, "async function _retryDownload", "// ── Serve auto-fix")

    assert "const sameSession = data.session_id === replaceSessionId" in retry
    assert "if (!sameSession) _tombstoneTask(replaceSessionId)" in retry
    assert "task.id = data.session_id" in retry
    assert "task.sessionId = data.session_id" in retry
    assert "task._userStopped = false" in retry


def test_stale_progress_restart_uses_retry_replacement_bookkeeping():
    source = RUNNING_JS.read_text(encoding="utf-8")
    stale_restart = _between(
        source,
        "badge.textContent = _startupStalled ? '0% stall — retrying' : 'stale — restarting'",
        "badge.textContent = 'stale — restart failed'",
    )

    assert "await _retryDownload(task.name, dlPayload, task.sessionId)" in stale_restart
    assert "fetch('/api/model/download'" not in stale_restart
    assert "_updateTask(task.sessionId, { sessionId:" not in stale_restart
